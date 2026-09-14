"""`/state` 조립 — 타임라인 · 경고 · 충돌 · 진행률 (DSN-21 · 설계서 §6.14 · §12 F1).

**여기에 "지금"을 넣지 마라.** ETag 를 `trip.revision` 으로 만들기 때문에, 응답 본문이
서버 현재 시각에 따라 달라지면 **304 가 거짓말을 한다** — 내용이 바뀌었는데 안 바뀌었다고
답하고, 클라이언트는 영영 갱신하지 않으며, 에러 로그도 남지 않는다(§12 F1).

그래서 이 모듈은 `Clock` 을 **받지 않는다**. 계산 입력은 일자의 `date`·`start_local` 과
스팟 데이터뿐이다. 현지 시계·"지금 열려 있나"는 클라이언트 몫이고, 날씨·환율은
`/state` 가 아니라 별도 엔드포인트다.

도메인 호출은 전부 이 파일의 얇은 래퍼(`_leg_of`, `_resolve_dwell` …)를 지난다 —
도메인 시그니처가 흔들려도 고칠 자리가 한 곳이다(설계서 §6.3~§6.12 가 SSoT).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from harbor_lantern.config import Settings, TravelConfig
from harbor_lantern.domain import conflict as conflict_engine
from harbor_lantern.domain import geo, hours, timeline, travel, warn
from harbor_lantern.domain.models import HoursSpec, LatLng, ScheduledSpot, SpotInput
from harbor_lantern.domain.util import (
    day_offset,
    format_hhmm,
    parse_hhmm,
    round_half_up,
    weekday_of,
)
from harbor_lantern.storage import repo_expenses, repo_spots, repo_trips

__all__ = [
    "build_state",
    "hours_payload",
    "participant_payload",
    "progress_payload",
    "spot_payload",
    "trip_payload",
]


# ── 작은 조각들 ───────────────────────────────────────────────────────────
def trip_payload(trip: sqlite3.Row) -> dict[str, Any]:
    from harbor_lantern.domain.invite import format_code

    return {
        "id": str(trip["id"]),
        "name": str(trip["name"]),
        "start_date": str(trip["start_date"]),
        "base_currency": str(trip["base_currency"]),
        "invite_code": str(trip["invite_code"]),
        "invite_code_display": format_code(str(trip["invite_code"])),
        "revision": int(trip["revision"]),
        "created_at": str(trip["created_at"]),
    }


def participant_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "display_name": str(row["display_name"]),
        "is_organizer": bool(row["is_organizer"]),
        "joined_at": str(row["joined_at"]),
    }


def progress_payload(done: int, total: int) -> dict[str, Any]:
    """`percent = round_half_up(done/total*100)`. `total == 0` 이면 0 (AC-030).

    내장 `round()` 는 은행가 반올림이라 참조 HTML 의 `Math.round` 와 특정 개수에서만
    갈린다 — 그래서 `round_half_up` 하나만 쓴다(§12 F4).
    """
    percent = 0 if total <= 0 else round_half_up(done / total * 100)
    return {"done": done, "total": total, "percent": percent}


def hours_payload(spec: HoursSpec) -> dict[str, Any]:
    """도메인 `HoursSpec` → API 표현.

    도메인은 상태를 셋(`open_range`·`open_24h`·`unknown`)만 갖고 요일별 개방은
    `weekday_open` 으로 표현한다(설계서 §6.2). 계약(openapi)은 그 경우를
    `weekday_range` 라는 이름으로 노출하므로 **여기서 한 번만** 옮긴다.
    """
    weekday_open = getattr(spec, "weekday_open", None)
    status = str(spec.status)
    if weekday_open:
        status = "weekday_range"
    open_min = getattr(spec, "open_min", None)
    close_min = getattr(spec, "close_min", None)
    return {
        "status": status,
        "approximate": bool(getattr(spec, "approximate", False)),
        "open_local": None if status == "weekday_range" or open_min is None else format_hhmm(open_min),
        "close_local": None if status == "weekday_range" or close_min is None else format_hhmm(close_min),
        "crosses_midnight": bool(getattr(spec, "crosses_midnight", False)),
        "pattern": str(getattr(spec, "pattern", "") or ""),
        "weekday_open": (
            None
            if not weekday_open
            else {str(day): [format_hhmm(pair[0]), format_hhmm(pair[1])] for day, pair in sorted(weekday_open.items())}
        ),
    }


def done_payload(visit: sqlite3.Row | None, names: Mapping[str, str]) -> dict[str, Any]:
    if visit is None:
        return {"is_done": False, "by_participant_id": None, "by_display_name": None, "at": None}
    participant_id = str(visit["done_by"])
    return {
        "is_done": True,
        "by_participant_id": participant_id,
        "by_display_name": names.get(participant_id),
        "at": str(visit["done_at"]),
    }


def spot_payload(
    row: sqlite3.Row,
    *,
    day_index: int,
    visit: sqlite3.Row | None,
    names: Mapping[str, str],
    travel_cfg: TravelConfig,
) -> dict[str, Any]:
    """스팟 한 건의 API 표현 (`Spot` 스키마). 영업시간·휴무는 **런타임 파싱**이다(A1)."""
    spec = hours.parse_hours(str(row["hours_text"]))
    closed = hours.parse_closed(str(row["closed_text"]))
    dwell_minutes, dwell_source = _resolve_dwell(_spot_input(row), travel_cfg)
    return {
        "id": str(row["id"]),
        "day_index": day_index,
        "position": int(row["position"]),
        "time_label": str(row["time_label"]),
        "fixed_start_local": row["fixed_start_local"],
        "name": str(row["name"]),
        "name_original": str(row["name_original"]),
        "tip": str(row["tip"]),
        "hours_text": str(row["hours_text"]),
        "closed_text": str(row["closed_text"]),
        "description": str(row["description"]),
        "recommendation": str(row["recommendation"]),
        "lat": float(row["lat"]),
        "lng": float(row["lng"]),
        "dwell_minutes": dwell_minutes,
        "dwell_source": dwell_source,
        "version": int(row["version"]),
        "done": done_payload(visit, names),
        "hours": hours_payload(spec),
        "closed_weekdays": sorted(closed.weekdays),
        "directions_url": geo.directions_url(float(row["lat"]), float(row["lng"])),
        "updated_at": str(row["updated_at"]),
    }


# ── /state 전체 ───────────────────────────────────────────────────────────
def build_state(conn: sqlite3.Connection, trip: sqlite3.Row, settings: Settings) -> dict[str, Any]:
    trip_id = str(trip["id"])
    participants = repo_trips.list_participants(conn, trip_id)
    names = {str(row["id"]): str(row["display_name"]) for row in participants}
    days = repo_trips.list_days(conn, trip_id)
    spots = repo_spots.list_spots_by_trip(conn, trip_id)
    visits = repo_spots.list_visits(conn, trip_id)

    by_day: dict[int, list[sqlite3.Row]] = {}
    for row in spots:
        by_day.setdefault(int(row["day_index"]), []).append(row)

    day_states: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for day in days:
        day_index = int(day["day_index"])
        day_rows = by_day.get(day_index, [])
        state, day_warnings, day_conflicts = _build_day(day, day_index, day_rows, visits, names, settings.travel)
        day_states.append(state)
        warnings.extend(day_warnings)
        conflicts.extend(day_conflicts)

    total_spots = len(spots)
    done_spots = sum(1 for row in spots if str(row["id"]) in visits)
    expenses = repo_expenses.list_expenses(conn, trip_id)
    return {
        "trip": trip_payload(trip),
        "participants": [participant_payload(row) for row in participants],
        "progress": progress_payload(done_spots, total_spots),
        "days": day_states,
        "warnings": warnings,
        "conflicts": conflicts,
        "expenses_summary": {
            "count": len(expenses),
            "total_minor": sum(int(row["amount_minor"]) for row in expenses),
            "currency": "HKD",
        },
    }


def _build_day(
    day: sqlite3.Row,
    day_index: int,
    rows: Sequence[sqlite3.Row],
    visits: Mapping[str, sqlite3.Row],
    names: Mapping[str, str],
    travel_cfg: TravelConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    date_iso = str(day["date"])
    start_min = parse_hhmm(str(day["start_local"])) or 0
    inputs = [_spot_input(row) for row in rows]
    scheduled, legs = timeline.build_timeline(start_min, inputs, travel_cfg)

    specs = {str(row["id"]): hours.parse_hours(str(row["hours_text"])) for row in rows}
    closed = {str(row["id"]): hours.parse_closed(str(row["closed_text"])) for row in rows}
    weekday = weekday_of(date_iso)

    payload_spots: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        spot_id = str(row["id"])
        payload = spot_payload(
            row,
            day_index=day_index,
            visit=visits.get(spot_id),
            names=names,
            travel_cfg=travel_cfg,
        )
        payload["schedule"] = _schedule_payload(scheduled[index]) if index < len(scheduled) else None
        payload["leg_to_next"] = _leg_payload(legs[index] if index < len(legs) else None, rows, index)
        payload_spots.append(payload)

    real_legs = [leg for leg in legs if leg is not None]
    distance_m, travel_minutes = travel.day_totals(real_legs)
    state = {
        "day_index": day_index,
        "date": date_iso,
        "weekday": weekday,
        "title": str(day["title"]),
        "area": str(day["area"]),
        "color": str(day["color"]),
        "start_local": str(day["start_local"]),
        "spots": payload_spots,
        "totals": {
            "distance_m": distance_m,
            "travel_minutes": travel_minutes,
            "dwell_minutes": sum(item.dwell_minutes for item in scheduled),
        },
    }
    return state, _warnings_payload(scheduled, specs, closed, weekday), _conflicts_payload(scheduled, rows)


def _warnings_payload(
    scheduled: Sequence[ScheduledSpot],
    specs: Mapping[str, HoursSpec],
    closed: Mapping[str, Any],
    weekday: int,
) -> list[dict[str, Any]]:
    """경고 생성은 도메인이 한다. 여기서는 개방시각을 붙여 계약 모양으로 옮길 뿐이다.

    `hours.status == "unknown"` 인 스팟에는 경고가 없다 — 거짓 경고 하나가 진짜 경고
    전부를 무시하게 만든다(AC-024 · §12 F9). 그 판단은 도메인 안에 있다.
    """
    payload: list[dict[str, Any]] = []
    for item in warn.build_warnings(scheduled, specs, closed, weekday):
        spec = specs.get(item.spot_id)
        open_min = getattr(spec, "open_min", None) if spec is not None else None
        close_min = getattr(spec, "close_min", None) if spec is not None else None
        payload.append(
            {
                "spot_id": item.spot_id,
                "kind": item.kind,
                "message": item.message,
                "eta_local": item.eta_local,
                "open_local": None if open_min is None else format_hhmm(open_min),
                "close_local": None if close_min is None else format_hhmm(close_min),
            }
        )
    return payload


def _conflicts_payload(scheduled: Sequence[ScheduledSpot], rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    fixed: dict[str, int] = {}
    for row in rows:
        value = row["fixed_start_local"]
        minute = parse_hhmm(str(value)) if value else None
        if minute is not None:
            fixed[str(row["id"])] = minute
    if not fixed:
        return []
    return [
        {
            "spot_id": item.spot_id,
            "fixed_spot_id": item.fixed_spot_id,
            "overlap_start_local": item.overlap_start_local,
            "overlap_end_local": item.overlap_end_local,
            "overlap_minutes": item.overlap_minutes,
        }
        for item in conflict_engine.find_conflicts(scheduled, fixed)
    ]


def _schedule_payload(item: ScheduledSpot) -> dict[str, Any]:
    return {
        "eta_local": format_hhmm(item.eta_min),
        "depart_local": format_hhmm(item.depart_min),
        "eta_day_offset": day_offset(item.eta_min),
        "depart_day_offset": day_offset(item.depart_min),
    }


def _leg_payload(leg: Any, rows: Sequence[sqlite3.Row], index: int) -> dict[str, Any] | None:
    """`leg_to_next` — 마지막 스팟이면 null (계약 `SpotInDay`).

    `build_timeline` 이 돌려주는 구간 목록은 i → i+1 순서다(§6.8 의 점화식).
    """
    if leg is None or index + 1 >= len(rows):
        return None
    return {
        "to_spot_id": str(rows[index + 1]["id"]),
        "distance_m": leg.distance_m,
        "mode": leg.mode,
        "travel_seconds": leg.travel_seconds,
        "overhead_seconds": leg.overhead_seconds,
        "minutes": leg.minutes,
        "estimated": True,
    }


# ── 도메인 호출 래퍼 (시그니처가 흔들리면 여기만 고친다) ───────────────────
def _spot_input(row: sqlite3.Row) -> SpotInput:
    return SpotInput(
        spot_id=str(row["id"]),
        coord=LatLng(float(row["lat"]), float(row["lng"])),
        time_label=str(row["time_label"]),
        dwell_minutes=row["dwell_minutes"],
        fixed_start_local=row["fixed_start_local"],
    )


def _resolve_dwell(spot: SpotInput, travel_cfg: TravelConfig) -> tuple[int, str]:
    minutes, source = timeline.resolve_dwell(spot)
    return int(minutes), str(source)
