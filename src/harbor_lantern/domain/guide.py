"""가이드 기반 일정 생성 — DSN-43 (설계서 §16.14 · REQ-023 · REQ-026 · AC-066~068 · AC-073 · AC-074).

구운 도시 파일(§16.11)의 스팟만으로 일정을 만든다. **현재 시각을 읽지 않는다** — 날짜는
인자다. 응답의 뼈대는 기존 `planner.build_plan` 과 같다(`days[].stops[]`·`arrival`·
`departure`·`travel_minutes`·`distance_m`·`hours_status`). 화면 렌더러를 둘로 가르지 않기
위해서다. 더해지는 것은 등급·출처와 하루의 `title`·`area`·`color` 뿐이다.

**R8(하루 테마 ↔ 대표 장소 충돌) 해소 순서가 이 모듈의 핵심이다 — AC-068 이 AC-073 보다
우선한다.** 중요도 상위 `reserve_top_n` 개를 먼저 자리로 예약하고, 클러스터링은 그 예약분을
씨앗으로 삼는다. 예약 스팟이 그날 지역과 어긋나면 **그대로 넣고 `area_exception` 을 표시**한다.
조용히 버리지도, 조용히 섞지도 않는다 — 파리 일정에서 에펠탑이 빠지는 것이 그 반대쪽 실패다.

**저녁 슬롯은 일몰이 아니라 고정 19:00 이다**(O13). 날짜·위도로 일몰을 근사하면 원천이 없는
값이 도메인에 들어온다. 대신 그 사실을 `notice` 에 적는다 — A8(이동시간 추정)에서 쓴 방식이다.
**저녁에 닫는 것으로 "확인된" 스팟만** 저녁 슬롯에서 뺀다. 영업시간을 읽지 못한 스팟은 빼지
않는다 — 못 읽은 것을 근거로 배제하는 것은 거짓 경고를 만드는 것과 같은 실수다(R1 · AC-024).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from harbor_lantern.config import DEFAULT_GUIDE_CONFIG, GuideConfig
from harbor_lantern.domain.cluster import cluster_spots
from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.planner import opening_windows, travel_minutes

__all__ = ["ALGORITHM", "NOTICE", "build_guide_plan"]

ALGORITHM = "guide-cluster-heuristic"
NOTICE = (
    "구운 도시 가이드 데이터로 만든 동선입니다. 이동은 추정치이고 저녁 배치는 19:00 기준 "
    "추정이며, 공휴일·임시휴무는 방문 전 확인하세요."
)

# 관심사 → 제외할 분류. 기존 계약(`mixed|culture|nature`)을 그대로 쓰고 우리 `category` 로만 옮긴다.
_INTEREST_EXCLUDES: Mapping[str, frozenset[str]] = {
    "culture": frozenset({"park", "viewpoint"}),
    "nature": frozenset({"museum", "gallery"}),
}


def build_guide_plan(
    city: Mapping[str, Any],
    spots: Sequence[Mapping[str, Any]],
    start: date,
    end: date,
    pace: str = "balanced",
    interests: str = "mixed",
    cfg: GuideConfig = DEFAULT_GUIDE_CONFIG,
) -> dict[str, Any]:
    """구운 도시 하나로 일정을 만든다 (§16.14).

    결정론(AC-067)은 세 가지로 보장한다 — ① 시계를 읽지 않고 ② 모든 정렬에
    타이브레이크(`spot id`)가 있고 ③ 집합 순회가 아니라 정렬된 시퀀스 순회다.
    여행 전체에서 같은 스팟은 두 번 배정되지 않는다(`used` · 기존 플래너와 같은 규칙).
    """
    per_day = dict(cfg.pace_spots)[pace]
    day_count = (end - start).days + 1
    ordered = sorted(spots, key=lambda spot: (-_sitelinks_of(spot), _id_of(spot)))

    reserve_n = min(cfg.reserve_top_n, max(0, day_count) * per_day)
    reserved_ids = {_id_of(spot) for spot in ordered[:reserve_n]}
    # 관심사 필터는 **예약분에 적용하지 않는다** — AC-068 이 AC-073 보다 우선한다.
    pool = [spot for spot in ordered if _id_of(spot) in reserved_ids or _passes_interests(spot, interests)]
    by_id = {_id_of(spot): spot for spot in pool}

    clusters = cluster_spots(pool, day_count, per_day, cfg)
    picks: list[list[Mapping[str, Any]]] = []
    used: set[str] = set()
    for index in range(max(0, day_count)):
        cluster = clusters[index] if index < len(clusters) else None
        ids = [] if cluster is None else [sid for sid in cluster.spot_ids if sid not in used]
        # 예약분이 먼저 슬롯을 가져간다. 그래야 넘치는 클러스터에서 잘리는 쪽이 꼬리가 된다.
        chosen_ids = [sid for sid in ids if sid in reserved_ids][:per_day]
        for sid in ids:
            if len(chosen_ids) >= per_day:
                break
            if sid not in chosen_ids:
                chosen_ids.append(sid)
        used.update(chosen_ids)
        picks.append([by_id[sid] for sid in chosen_ids])

    seated_elsewhere = _seat_leftover_reserved(picks, ordered, reserved_ids, used, by_id, per_day)

    days: list[dict[str, Any]] = []
    for index in range(max(0, day_count)):
        cluster = clusters[index] if index < len(clusters) else None
        day = start + timedelta(days=index)
        days.append(_build_day(day, index, picks[index], cluster, cfg, seated_elsewhere))

    scheduled = sum(len(entry["stops"]) for entry in days)
    center = city.get("center") or {}
    return {
        "destination": {
            "city_id": city.get("city_id", ""),
            "name": city.get("name_ko", ""),
            "lat": center.get("lat"),
            "lng": center.get("lng"),
        },
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": days,
        "algorithm": ALGORITHM,
        "notice": NOTICE,
        # AC-085 — 등급은 **항상** 실린다. 비어 있으면 화면이 아무 문구도 못 고른다.
        "guide_grade": str(city.get("grade") or ""),
        "guide_source": {
            "city_id": city.get("city_id", ""),
            "retrieved_at": city.get("retrieved_at", ""),
            "sources": list(city.get("sources", ())),
            "known_gaps": list(city.get("known_gaps", ())),
        },
        "candidate_count": len(spots),
        "scheduled_count": scheduled,
    }


def _seat_leftover_reserved(
    picks: list[list[Mapping[str, Any]]],
    ordered: Sequence[Mapping[str, Any]],
    reserved_ids: set[str],
    used: set[str],
    by_id: Mapping[str, Mapping[str, Any]],
    per_day: int,
) -> set[str]:
    """어느 하루에도 못 앉은 **예약 스팟**을 빈 슬롯에 앉힌다 (§16.14-3 · AC-068).

    한 클러스터에 예약분이 몰리면 `per_day` 를 넘어 밀려나는 것이 생긴다. 그때 상위
    포함이 이긴다 — 자기 클러스터가 아니어도 넣는다. 돌려주는 것은 **그렇게 앉힌 id 들**이며,
    그 하루는 `area_exception` 을 달고 나간다(AC-073 후단).
    """
    seated: set[str] = set()
    for spot in ordered:
        sid = _id_of(spot)
        if sid not in reserved_ids or sid in used or sid not in by_id:
            continue
        for slot in picks:
            if len(slot) < per_day:
                slot.append(spot)
                used.add(sid)
                seated.add(sid)
                break
    return seated


def _build_day(
    day: date,
    index: int,
    chosen: Sequence[Mapping[str, Any]],
    cluster: Any,
    cfg: GuideConfig,
    seated_elsewhere: set[str],
) -> dict[str, Any]:
    """하루 하나를 시간까지 채운다. 동선은 대표 스팟에서 출발하는 최근접 순회다.

    저녁 후보가 있으면 **그날의 마지막 자리**로 보낸다(AC-074). 마지막 자리의 도착은
    `evening_from_min`(19:00) 이전으로 당기지 않는다 — 야경을 낮에 배정하면 그 슬롯의
    의미가 사라진다.
    """
    area = "" if cluster is None else cluster.area
    color = "" if cluster is None else cluster.color
    title = "" if cluster is None else cluster.title
    route = _route(chosen, day.weekday(), cfg)

    stops: list[dict[str, Any]] = []
    exceptions: list[str] = []
    cursor = cfg.day_start_min
    position: LatLng | None = None
    for order, (spot, is_evening) in enumerate(route):
        coord = _coord(spot)
        distance = 0.0 if position is None else haversine_m(position, coord)
        travel = 0 if position is None else travel_minutes(distance)
        arrival = cursor + travel
        if is_evening:
            arrival = max(arrival, cfg.evening_from_min)
        departure = arrival + cfg.spot_dwell_min
        if departure > cfg.day_end_min:
            break  # 하루 창(21:00)을 넘기지 않는다. 남는 스팟은 배정하지 않는다.
        windows = opening_windows(str(spot.get("hours_text", "") or ""), day.weekday())
        stops.append({
            "place": spot,
            "guide_id": _id_of(spot),
            "arrival": _hhmm(arrival),
            "departure": _hhmm(departure),
            "travel_minutes": travel if order else 0,
            "distance_m": round(distance) if order else 0,
            "hours_status": "weekly_hours" if windows is not None else "unverified",
            "evening_slot": is_evening,
        })
        spot_area = str(spot.get("area", "") or "").strip()
        outside_area = bool(area) and bool(spot_area) and spot_area != area
        if outside_area or _id_of(spot) in seated_elsewhere:
            exceptions.append(str(spot.get("name", "")))
        position = coord
        cursor = departure

    return {
        "date": day.isoformat(),
        "weekday": day.weekday(),
        "title": title,
        "area": area,
        "color": color,
        "stops": stops,
        "distance_m": sum(stop["distance_m"] for stop in stops),
        "travel_minutes": sum(stop["travel_minutes"] for stop in stops),
        # AC-073 후단 — 다른 지역 스팟이 들어왔다는 사실을 **응답에 표시한다**.
        "area_exception": bool(exceptions),
        # 문구는 **일어난 일만** 말한다. 이유를 단정하면(예: "중요도 우선 배정") 지역 라벨이
        # 촘촘한 도시에서 매일 같은 거짓 사유가 붙는다 — 파리의 구(區)가 그런 경우다.
        "area_exception_reason": (
            f"이 날에는 {area or '대표 지역'} 밖의 스팟이 함께 배정됐습니다 — {', '.join(exceptions)}."
            if exceptions
            else ""
        ),
        "day_index": index,
    }


def _route(
    chosen: Sequence[Mapping[str, Any]],
    weekday: int,
    cfg: GuideConfig,
) -> list[tuple[Mapping[str, Any], bool]]:
    """방문 순서 — 대표(중요도 1위)에서 시작하는 최근접 순회. 동률은 `spot id` 로 깬다.

    저녁 후보는 마지막으로 뺀다. 후보 판정은 `evening_candidate` 필드나 `category` 에서
    유도하며, **저녁에 닫는 것으로 확인된 것만** 제외한다(읽지 못한 영업시간은 근거가 아니다).
    """
    if not chosen:
        return []
    remaining = list(chosen)
    evening = _evening_pick(remaining, weekday, cfg)
    if evening is not None:
        evening_id = _id_of(evening)
        remaining = [spot for spot in remaining if _id_of(spot) != evening_id]

    route: list[tuple[Mapping[str, Any], bool]] = []
    if remaining:
        current = remaining.pop(0)  # 중요도 1위에서 출발한다 (chosen 은 중요도 순이다)
        route.append((current, False))
        while remaining:
            nearest = min(
                remaining,
                key=lambda spot, base=current: (haversine_m(_coord(base), _coord(spot)), _id_of(spot)),
            )
            remaining.remove(nearest)
            route.append((nearest, False))
            current = nearest
    if evening is not None:
        route.append((evening, True))
    return route


def _evening_pick(
    chosen: Sequence[Mapping[str, Any]],
    weekday: int,
    cfg: GuideConfig,
) -> Mapping[str, Any] | None:
    """저녁 슬롯에 앉힐 스팟. 없으면 `None` — 없는데 억지로 만들지 않는다."""
    for spot in chosen:  # chosen 은 중요도 순이므로 상위 후보가 먼저 뽑힌다
        if not _is_evening_candidate(spot, cfg):
            continue
        windows = opening_windows(str(spot.get("hours_text", "") or ""), weekday)
        if windows is not None and not any(a <= cfg.evening_from_min < b for a, b in windows):
            continue  # **닫는 것으로 확인된** 경우만 뺀다
        return spot
    return None


def _is_evening_candidate(spot: Mapping[str, Any], cfg: GuideConfig) -> bool:
    if spot.get("evening_candidate") is True:
        return True
    return str(spot.get("category", "")) in cfg.evening_categories


def _passes_interests(spot: Mapping[str, Any], interests: str) -> bool:
    excluded = _INTEREST_EXCLUDES.get(interests)
    return excluded is None or str(spot.get("category", "")) not in excluded


def _hhmm(minute_of_day: int) -> str:
    return f"{minute_of_day // 60:02}:{minute_of_day % 60:02}"


def _coord(spot: Mapping[str, Any]) -> LatLng:
    return LatLng(float(spot["lat"]), float(spot["lng"]))


def _sitelinks_of(spot: Mapping[str, Any]) -> int:
    importance = spot.get("importance")
    if isinstance(importance, Mapping):
        return int(importance.get("sitelinks", 0) or 0)
    return int(spot.get("sitelinks", 0) or 0)


def _id_of(spot: Mapping[str, Any]) -> str:
    return str(spot.get("id") or spot.get("wikidata_id") or "")
