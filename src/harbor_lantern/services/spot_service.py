"""스팟 CRUD · 재정렬 · 일자 간 이동 · 완료 체크 · 동선 최적화 — DSN-09~DSN-11 · DSN-19.

**두 개의 "버전"을 섞지 마라**(§5.2 · §12 F3).

| 이름 | 어디에 | 쓰는 곳 |
|------|--------|--------|
| `spot.version` | 리소스 행 | `PATCH` 본문의 `version` — 낙관적 잠금, 불일치 시 409 + 최신 리소스 |
| `trip.revision` | 여행 행 | 재정렬·이동 본문의 `expected_revision` — 동기화, 모든 변경에서 +1 |

둘 다 정수 카운터라 타입 검사가 안 잡는다. 바꿔 쓰면 409 가 아예 안 나거나 정상 요청마다 409 다.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from harbor_lantern.api.errors import NotFoundError, RevisionConflictError, UnprocessableError, VersionConflictError
from harbor_lantern.clock import Clock
from harbor_lantern.config import Settings
from harbor_lantern.domain import route
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.util import format_iso_utc, is_hhmm
from harbor_lantern.services.plan_service import progress_payload, spot_payload
from harbor_lantern.storage import repo_spots, repo_trips
from harbor_lantern.storage.db import transaction

__all__ = [
    "create_spot",
    "delete_spot",
    "move_spot",
    "optimize_day",
    "reorder_day",
    "set_done",
    "spot_response",
    "update_spot",
]


def spot_response(conn: sqlite3.Connection, trip_id: str, spot_id: str, settings: Settings) -> dict[str, Any]:
    row = repo_spots.get_spot_with_day(conn, trip_id, spot_id)
    if row is None:
        raise NotFoundError("스팟을 찾을 수 없습니다.")
    names = {str(item["id"]): str(item["display_name"]) for item in repo_trips.list_participants(conn, trip_id)}
    return spot_payload(
        row,
        day_index=int(row["day_index"]),
        visit=repo_spots.get_visit(conn, spot_id),
        names=names,
        travel_cfg=settings.travel,
    )


def create_spot(
    conn: sqlite3.Connection,
    clock: Clock,
    settings: Settings,
    *,
    trip: sqlite3.Row,
    day_index: int,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """해당 일자 **목록 끝**에 추가한다 (REQ-004 · AC-006)."""
    trip_id = str(trip["id"])
    day = _require_day(conn, trip_id, day_index)
    now = format_iso_utc(clock.now_utc())
    spot_id = uuid.uuid4().hex
    time_label = str(fields["time_label"])
    with transaction(conn):
        repo_spots.insert_spot(
            conn,
            spot_id=spot_id,
            trip_id=trip_id,
            day_id=str(day["id"]),
            position=repo_spots.next_position(conn, str(day["id"])),
            time_label=time_label,
            # 시간대 라벨이 `HH:MM` 이면 고정시각 일정이다 — 앵커가 여기서 유도된다(§5.3).
            fixed_start_local=time_label if is_hhmm(time_label) else None,
            name=str(fields["name"]),
            name_original=str(fields["name_original"]),
            tip=str(fields["tip"]),
            hours_text=str(fields["hours_text"]),
            closed_text=str(fields["closed_text"]),
            description=str(fields["description"]),
            recommendation=str(fields["recommendation"]),
            lat=float(fields["lat"]),
            lng=float(fields["lng"]),
            dwell_minutes=fields.get("dwell_minutes"),
            created_at=now,
            updated_at=now,
        )
        repo_trips.bump_revision(conn, trip_id)
    return spot_response(conn, trip_id, spot_id, settings)


def update_spot(
    conn: sqlite3.Connection,
    clock: Clock,
    settings: Settings,
    *,
    trip: sqlite3.Row,
    spot_id: str,
    version: int,
    changes: Mapping[str, Any],
) -> dict[str, Any]:
    """낙관적 잠금 수정 (REQ-004 · NFR-012 · AC-007 · AC-047).

    버전이 다르면 **아무것도 덮어쓰지 않고** 409 + 최신 리소스를 돌려준다.
    """
    trip_id = str(trip["id"])
    if repo_spots.get_spot(conn, trip_id, spot_id) is None:
        raise NotFoundError("스팟을 찾을 수 없습니다.")

    fields = dict(changes)
    if "time_label" in fields:
        label = str(fields["time_label"])
        # 라벨이 고정시각으로 바뀌면 앵커도 함께 바뀐다 — 둘이 갈라지면 충돌 검사가
        # 화면과 다른 것을 본다.
        fields["fixed_start_local"] = label if is_hhmm(label) else None

    now = format_iso_utc(clock.now_utc())
    with transaction(conn):
        updated = repo_spots.update_spot(
            conn,
            spot_id=spot_id,
            expected_version=version,
            fields=fields,
            updated_at=now,
        )
        if not updated:
            raise VersionConflictError(spot_response(conn, trip_id, spot_id, settings))
        repo_trips.bump_revision(conn, trip_id)
    return spot_response(conn, trip_id, spot_id, settings)


def delete_spot(conn: sqlite3.Connection, clock: Clock, *, trip: sqlite3.Row, spot_id: str) -> None:
    """삭제 후 남은 스팟의 순서를 0..n-1 로 재부여한다 — 빈 자리를 남기지 않는다(AC-008).

    진행률 분모가 1 줄어드는 것(AC-007)은 `visit` 의 `ON DELETE CASCADE` 와
    분모를 `COUNT(spot)` 로 세는 것으로 자동 충족된다.
    """
    trip_id = str(trip["id"])
    row = repo_spots.get_spot(conn, trip_id, spot_id)
    if row is None:
        raise NotFoundError("스팟을 찾을 수 없습니다.")
    day_id = str(row["day_id"])
    now = format_iso_utc(clock.now_utc())
    with transaction(conn):
        repo_spots.delete_spot(conn, spot_id)
        repo_spots.renumber_day(conn, day_id, repo_spots.list_spot_ids_of_day(conn, day_id), now)
        repo_trips.bump_revision(conn, trip_id)


def reorder_day(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    trip: sqlite3.Row,
    day_index: int,
    expected_revision: int,
    spot_ids: Sequence[str],
) -> dict[str, Any]:
    """일자 내 재배열 — 원자 (REQ-005 · AC-008 · AC-047).

    `spot_ids` 가 그 일자의 스팟 **전체 집합과 정확히 일치**하지 않으면 422 이고
    **아무것도 쓰지 않는다.** 부분 반영은 없다.
    """
    trip_id = str(trip["id"])
    day = _require_day(conn, trip_id, day_index)
    _require_revision(trip, expected_revision)

    current = repo_spots.list_spot_ids_of_day(conn, str(day["id"]))
    _require_same_set(spot_ids, current)

    now = format_iso_utc(clock.now_utc())
    with transaction(conn):
        repo_spots.renumber_day(conn, str(day["id"]), list(spot_ids), now)
        revision = repo_trips.bump_revision(conn, trip_id)
    return {"day_index": day_index, "spot_ids": list(spot_ids), "revision": revision}


def move_spot(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    trip: sqlite3.Row,
    spot_id: str,
    expected_revision: int,
    to_day_index: int,
    to_position: int | None,
) -> dict[str, Any]:
    """일자 간 이동 (REQ-005 · AC-009) — 원본·대상 두 일자를 같은 트랜잭션에서 재부여한다."""
    trip_id = str(trip["id"])
    row = repo_spots.get_spot_with_day(conn, trip_id, spot_id)
    if row is None:
        raise NotFoundError("스팟을 찾을 수 없습니다.")
    target_day = _require_day(conn, trip_id, to_day_index)
    _require_revision(trip, expected_revision)

    from_day_index = int(row["day_index"])
    from_day_id = str(row["day_id"])
    to_day_id = str(target_day["id"])
    now = format_iso_utc(clock.now_utc())

    source_order = [item for item in repo_spots.list_spot_ids_of_day(conn, from_day_id) if item != spot_id]
    if from_day_id == to_day_id:
        target_order = list(source_order)
        target_order.insert(_clamp(to_position, len(target_order)), spot_id)
        with transaction(conn):
            repo_spots.renumber_day(conn, to_day_id, target_order, now)
            revision = repo_trips.bump_revision(conn, trip_id)
    else:
        target_order = repo_spots.list_spot_ids_of_day(conn, to_day_id)
        target_order.insert(_clamp(to_position, len(target_order)), spot_id)
        with transaction(conn):
            repo_spots.move_spot_between_days(
                conn,
                spot_id=spot_id,
                from_day_id=from_day_id,
                to_day_id=to_day_id,
                from_order=source_order,
                to_order=target_order,
                updated_at=now,
            )
            revision = repo_trips.bump_revision(conn, trip_id)
    return {
        "spot_id": spot_id,
        "from_day_index": from_day_index,
        "to_day_index": to_day_index,
        "revision": revision,
    }


def set_done(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    trip: sqlite3.Row,
    spot_id: str,
    done: bool,
    participant: sqlite3.Row,
) -> dict[str, Any]:
    """완료 체크 / 해제 — 멱등 (REQ-006 · AC-010 · AC-011).

    저장이 서버에 있으므로 참가자 B 의 조회에도 그대로 보인다.
    """
    trip_id = str(trip["id"])
    if repo_spots.get_spot(conn, trip_id, spot_id) is None:
        raise NotFoundError("스팟을 찾을 수 없습니다.")
    now = format_iso_utc(clock.now_utc())
    with transaction(conn):
        if done:
            repo_spots.set_visit(
                conn,
                spot_id=spot_id,
                trip_id=trip_id,
                done_by=str(participant["id"]),
                done_at=now,
            )
        else:
            repo_spots.clear_visit(conn, spot_id)
        revision = repo_trips.bump_revision(conn, trip_id)

    visit = repo_spots.get_visit(conn, spot_id)
    names = {str(item["id"]): str(item["display_name"]) for item in repo_trips.list_participants(conn, trip_id)}
    from harbor_lantern.services.plan_service import done_payload

    return {
        "spot_id": spot_id,
        "done": done_payload(visit, names),
        "progress": progress_payload(repo_spots.count_visits(conn, trip_id), repo_spots.count_spots(conn, trip_id)),
        "revision": revision,
    }


def optimize_day(
    conn: sqlite3.Connection,
    *,
    trip: sqlite3.Row,
    day_index: int,
) -> dict[str, Any]:
    """동선 최적화 **제안** (REQ-009 · AC-017 · AC-018).

    **쓰기를 하지 않는다.** 적용은 재정렬 엔드포인트가 한다 — 쓰기 경로를 늘리지 않는 것이
    §6.11 의 원자성 설계를 한 곳에 유지하는 방법이다.
    """
    trip_id = str(trip["id"])
    day = _require_day(conn, trip_id, day_index)
    rows = repo_spots.list_spots_by_day(conn, str(day["id"]))
    spot_ids = [str(row["id"]) for row in rows]
    coords = {str(row["id"]): LatLng(float(row["lat"]), float(row["lng"])) for row in rows}
    anchored = {str(row["id"]) for row in rows if row["fixed_start_local"]}

    proposal = route.optimize_day(spot_ids, coords, anchored)
    return {
        "day_index": day_index,
        "current_order": list(proposal.current_order),
        "proposed_order": list(proposal.proposed_order),
        "current_total_distance_m": proposal.current_total_distance_m,
        "proposed_total_distance_m": proposal.proposed_total_distance_m,
        "improved": proposal.improved,
        "algorithm": proposal.algorithm,
        "anchored_spot_ids": list(proposal.anchored_spot_ids),
    }


# ── 내부 ──────────────────────────────────────────────────────────────────
def _require_day(conn: sqlite3.Connection, trip_id: str, day_index: int) -> sqlite3.Row:
    day = repo_trips.get_day(conn, trip_id, day_index)
    if day is None:
        raise NotFoundError("일자를 찾을 수 없습니다.")
    return day


def _require_revision(trip: sqlite3.Row, expected_revision: int) -> None:
    current = int(trip["revision"])
    if expected_revision != current:
        raise RevisionConflictError(current)


def _require_same_set(given: Sequence[str], current: Sequence[str]) -> None:
    if len(given) != len(current) or set(given) != set(current) or len(set(given)) != len(given):
        raise UnprocessableError(
            "순서 목록이 해당 일자의 스팟 집합과 정확히 일치해야 합니다.",
            code="spot_set_mismatch",
            detail={"expected": list(current), "given": list(given)},
        )


def _clamp(position: int | None, size: int) -> int:
    if position is None or position > size:
        return size
    return max(0, position)
