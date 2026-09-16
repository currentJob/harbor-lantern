"""하루 테마 클러스터링 — DSN-42 (설계서 §16.13 · REQ-026 · O12 확정 · AC-073).

목표 형태는 홍콩 시드다 — **하루 = 한 지역**(Day 1 "구룡(TST)", Day 2 "홍콩섬 북부"…).
스팟을 지리적으로 묶어 하루치 후보를 만들고, 그 하루에 붙일 **제목·지역·색**을 낸다.

**제목은 편집 문구가 아니라 유도 라벨이다.** `"{지역} — {대표 스팟} 중심"` 고정 규칙으로
조립하며 담긴 것은 전부 데이터에 있는 사실이다. 지역명은 스팟들의 `P131` 한국어 라벨에서
최빈값을 고르고, 없으면 영어, 그것도 없으면 빈 문자열이다 — **지어내지 않는다**(REQ-025 의
원칙을 하루 제목에도 적용한다).

순수 함수뿐이다. 시계도 I/O 도 없다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from harbor_lantern.config import DEFAULT_GUIDE_CONFIG, GuideConfig
from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng

__all__ = ["Cluster", "cluster_spots"]


@dataclass(frozen=True)
class Cluster:
    """하루 하나. `spot_ids` 는 **중요도 순**이다(일정 생성이 그 순서로 슬롯을 채운다)."""

    index: int
    area: str
    title: str
    color: str
    spot_ids: tuple[str, ...]


def cluster_spots(
    spots: Sequence[Mapping[str, Any]],
    day_count: int,
    per_day: int,
    cfg: GuideConfig = DEFAULT_GUIDE_CONFIG,
) -> tuple[Cluster, ...]:
    """스팟들을 `day_count` 개의 하루 묶음으로 나눈다 (§16.13 의 1~6 단계 그대로).

    결정론이 규칙의 일부다(NFR-020 · AC-067): 중요도 정렬은 `(-sitelinks, id)`, 배정 동률은
    `(클러스터 index, spot id)`, 균형 조정의 반복 상한은 `스팟 수` 다 — **무한 루프가 없다는
    것이 코드로 보여야 한다.** 넘치는 스팟을 잘라내지는 않는다. 옮길 곳이 없으면 그대로 둔다.
    """
    ordered = _by_importance(spots)
    if not ordered or day_count <= 0:
        return ()

    seeds = _pick_seeds(ordered, day_count, cfg.seed_min_separation_m)
    members: list[list[Mapping[str, Any]]] = [[seed] for seed in seeds]
    seed_ids = {_id_of(seed) for seed in seeds}

    for spot in ordered:
        if _id_of(spot) in seed_ids:
            continue
        index = min(
            range(len(seeds)),
            key=lambda i: (haversine_m(_coord(spot), _coord(seeds[i])), i),
        )
        members[index].append(spot)

    _balance(members, per_day, len(ordered))

    clusters: list[Cluster] = []
    for index, group in enumerate(members):
        group.sort(key=lambda spot: (-_sitelinks_of(spot), _id_of(spot)))
        area = _area_of(group)
        representative = str(group[0].get("name", "")) if group else ""
        clusters.append(
            Cluster(
                index=index,
                area=area,
                title=_title(area, representative),
                color=cfg.day_palette[index % len(cfg.day_palette)] if cfg.day_palette else "",
                spot_ids=tuple(_id_of(spot) for spot in group),
            )
        )
    return tuple(clusters)


def _pick_seeds(
    ordered: Sequence[Mapping[str, Any]],
    day_count: int,
    separation_m: float,
) -> list[Mapping[str, Any]]:
    """중요도 상위부터 훑으며 서로 `separation_m` 이상 떨어진 씨앗을 고른다 (§16.13-1).

    모자라면 **이미 뽑힌 씨앗에서 가장 먼 것부터** 채운다 — 가까운 것을 채우면 두 씨앗이
    같은 거리를 두고 겹쳐 하루가 반으로 쪼개진다.
    """
    seeds: list[Mapping[str, Any]] = []
    for spot in ordered:
        if len(seeds) >= day_count:
            break
        if all(haversine_m(_coord(spot), _coord(seed)) >= separation_m for seed in seeds):
            seeds.append(spot)
    if len(seeds) >= day_count:
        return seeds

    chosen = {_id_of(seed) for seed in seeds}
    rest = [spot for spot in ordered if _id_of(spot) not in chosen]
    while rest and len(seeds) < day_count:
        if seeds:
            rest.sort(key=lambda spot: (-min(haversine_m(_coord(spot), _coord(s)) for s in seeds), _id_of(spot)))
        pick = rest.pop(0)
        seeds.append(pick)
    return seeds


def _balance(members: list[list[Mapping[str, Any]]], per_day: int, limit: int) -> None:
    """`per_day` 를 넘는 클러스터에서 **중심에서 가장 먼** 스팟을 여유 있는 곳으로 옮긴다.

    반복은 `limit`(= 스팟 수)회로 막는다. 옮기다 보면 받은 쪽이 넘치고 다시 되돌리는
    왕복이 생길 수 있는데, 상한이 그 자리에서 멈춘다 — 균형은 품질이고 종료는 계약이다.
    씨앗은 옮기지 않는다. 씨앗이 움직이면 그 하루의 지역 자체가 바뀐다.
    """
    if per_day <= 0:
        return
    for _ in range(max(1, limit)):
        over = [i for i, group in enumerate(members) if len(group) > per_day]
        if not over:
            return
        source = min(over)
        centroid = _centroid(members[source])
        movable = members[source][1:]  # [0] 은 씨앗이다
        if not movable:
            return
        far = max(movable, key=lambda spot: (haversine_m(_coord(spot), centroid), _id_of(spot)))
        targets = [i for i, group in enumerate(members) if i != source and len(group) < per_day]
        if not targets:
            return
        target = min(targets, key=lambda i: (haversine_m(_coord(far), _centroid(members[i])), i))
        members[source].remove(far)
        members[target].append(far)


def _area_of(group: Sequence[Mapping[str, Any]]) -> str:
    """클러스터의 지역명 — 최빈 `P131` 한국어 라벨. 동률이면 중요도 상위 스팟의 값 (§16.13-4).

    한국어가 하나도 없으면 영어 라벨(`area_en`)을 쓰고, 그것도 없으면 `""` 다.
    화면은 빈 값을 "지역 미상"으로 그린다 — **없는 지역명을 만들지 않는다.**
    """
    for key in ("area", "area_en"):
        counts: dict[str, int] = {}
        first_seen: dict[str, int] = {}
        for rank, spot in enumerate(group):
            value = str(spot.get(key, "") or "").strip()
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
            first_seen.setdefault(value, rank)
        if counts:
            best = max(counts.values())
            tied = [value for value, count in counts.items() if count == best]
            return min(tied, key=lambda value: first_seen[value])
    return ""


def _title(area: str, representative: str) -> str:
    if not representative:
        return area
    return f"{area} — {representative} 중심" if area else f"{representative} 주변"


def _by_importance(spots: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(spots, key=lambda spot: (-_sitelinks_of(spot), _id_of(spot)))


def _centroid(group: Sequence[Mapping[str, Any]]) -> LatLng:
    return LatLng(
        sum(float(spot["lat"]) for spot in group) / len(group),
        sum(float(spot["lng"]) for spot in group) / len(group),
    )


def _coord(spot: Mapping[str, Any]) -> LatLng:
    return LatLng(float(spot["lat"]), float(spot["lng"]))


def _sitelinks_of(spot: Mapping[str, Any]) -> int:
    importance = spot.get("importance")
    if isinstance(importance, Mapping):
        return int(importance.get("sitelinks", 0) or 0)
    return int(spot.get("sitelinks", 0) or 0)


def _id_of(spot: Mapping[str, Any]) -> str:
    return str(spot.get("id") or spot.get("wikidata_id") or "")
