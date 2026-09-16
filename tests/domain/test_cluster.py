"""하루 테마 클러스터링 — DSN-42 (§16.13 · REQ-026 · AC-073).

목표 형태는 홍콩 시드다 — **하루 = 한 지역**. 그래서 검사는 "묶였는가"가 아니라
"같은 하루의 스팟들이 같은 지역인가"와 "제목·색이 데이터에서 유도됐는가"다.
"""

from __future__ import annotations

from harbor_lantern.config import DEFAULT_GUIDE_CONFIG
from harbor_lantern.domain.cluster import cluster_spots

# 서로 멀리 떨어진 세 구역. 좌표는 세 무리를 만들기 위한 고정 입력이다.
NORTH = [
    {"id": "n1", "name": "북1", "lat": 35.720, "lng": 139.780, "area": "북구", "importance": {"sitelinks": 90}},
    {"id": "n2", "name": "북2", "lat": 35.722, "lng": 139.782, "area": "북구", "importance": {"sitelinks": 40}},
    {"id": "n3", "name": "북3", "lat": 35.724, "lng": 139.784, "area": "북구", "importance": {"sitelinks": 30}},
]
WEST = [
    {"id": "w1", "name": "서1", "lat": 35.690, "lng": 139.700, "area": "서구", "importance": {"sitelinks": 80}},
    {"id": "w2", "name": "서2", "lat": 35.692, "lng": 139.702, "area": "서구", "importance": {"sitelinks": 35}},
    {"id": "w3", "name": "서3", "lat": 35.694, "lng": 139.704, "area": "서구", "importance": {"sitelinks": 25}},
]
SOUTH = [
    {"id": "s1", "name": "남1", "lat": 35.620, "lng": 139.740, "area": "남구", "importance": {"sitelinks": 70}},
    {"id": "s2", "name": "남2", "lat": 35.622, "lng": 139.742, "area": "남구", "importance": {"sitelinks": 33}},
    {"id": "s3", "name": "남3", "lat": 35.624, "lng": 139.744, "area": "남구", "importance": {"sitelinks": 20}},
]
ALL = NORTH + WEST + SOUTH


def test_a_day_is_one_area() -> None:
    clusters = cluster_spots(ALL, 3, 3)
    assert len(clusters) == 3
    by_id = {spot["id"]: spot for spot in ALL}
    for cluster in clusters:
        areas = {by_id[sid]["area"] for sid in cluster.spot_ids}
        assert len(areas) == 1
        assert cluster.area in areas


def test_title_is_derived_not_written() -> None:
    """`"{지역} — {대표 스팟} 중심"` 고정 규칙. 대표는 그 클러스터의 중요도 1위다."""
    clusters = cluster_spots(ALL, 3, 3)
    titles = {cluster.title for cluster in clusters}
    assert titles == {"북구 — 북1 중심", "서구 — 서1 중심", "남구 — 남1 중심"}


def test_title_without_an_area_falls_back_to_the_representative() -> None:
    """지역명이 없으면 지어내지 않는다."""
    nameless = [dict(spot, area="") for spot in NORTH]
    assert cluster_spots(nameless, 1, 3)[0].title == "북1 주변"
    assert cluster_spots(nameless, 1, 3)[0].area == ""


def test_area_uses_the_most_common_label() -> None:
    mixed = [dict(NORTH[0], area="갑구"), dict(NORTH[1], area="을구"), dict(NORTH[2], area="을구")]
    assert cluster_spots(mixed, 1, 3)[0].area == "을구"


def test_english_area_is_used_only_when_korean_is_missing() -> None:
    english = [dict(spot, area="", area_en="Kita") for spot in NORTH]
    assert cluster_spots(english, 1, 3)[0].area == "Kita"


def test_colors_cycle_through_the_seed_palette() -> None:
    """같은 화면에서 두 가이드의 색 체계가 다르면 사용자는 색에 의미가 있다고 읽는다."""
    clusters = cluster_spots(ALL, 3, 3)
    assert [cluster.color for cluster in clusters] == list(DEFAULT_GUIDE_CONFIG.day_palette[:3])


def test_an_overfull_cluster_hands_its_farthest_spot_to_a_cluster_with_room() -> None:
    """한 지역에 몰린 입력도 하루 정원 안으로 들어온다(§16.13-3)."""
    crowded = NORTH + [
        {"id": f"n{i}", "name": f"북{i}", "lat": 35.726 + i / 1000, "lng": 139.786,
         "area": "북구", "importance": {"sitelinks": 10 - i}}
        for i in range(4, 8)
    ] + WEST[:1] + SOUTH[:1]
    clusters = cluster_spots(crowded, 3, 3)
    assert sum(len(cluster.spot_ids) for cluster in clusters) == len(crowded)
    assert [len(cluster.spot_ids) for cluster in clusters] == [3, 3, 3]


def test_spots_are_never_truncated_even_when_capacity_is_short() -> None:
    """정원보다 스팟이 많아도 **버리지 않는다** — 버리면 조용히 사라진다."""
    clusters = cluster_spots(ALL, 3, 2)
    assert sum(len(cluster.spot_ids) for cluster in clusters) == len(ALL)


def test_balancing_keeps_every_spot_exactly_once() -> None:
    clusters = cluster_spots(ALL, 2, 5)
    assigned = [sid for cluster in clusters for sid in cluster.spot_ids]
    assert sorted(assigned) == sorted(spot["id"] for spot in ALL)


def test_input_order_does_not_change_the_clusters() -> None:
    """결정론(AC-067) — 씨앗 선정도 배정도 동률 규칙을 갖는다."""
    assert cluster_spots(ALL, 3, 3) == cluster_spots(list(reversed(ALL)), 3, 3)


def test_more_days_than_spots_gives_fewer_clusters_not_empty_ones() -> None:
    """스팟보다 날이 많으면 빈 하루를 만들지 않는다 — 제목도 지역도 없는 하루는 지어낸 것이다.

    일정 생성(§16.14)이 없는 클러스터를 **빈 하루**로 그린다. 부분 등급 도시에서
    "일정 일부가 채워지지 않을 수 있다"(AC-085)가 실제로 그렇게 보이는 자리다.
    """
    clusters = cluster_spots(NORTH[:2], 3, 5)
    assert len(clusters) == 2
    assert sum(len(cluster.spot_ids) for cluster in clusters) == 2


def test_no_spots_gives_no_clusters() -> None:
    assert cluster_spots([], 3, 5) == ()


def test_spot_ids_inside_a_cluster_are_importance_ordered() -> None:
    cluster = cluster_spots(NORTH, 1, 5)[0]
    assert cluster.spot_ids == ("n1", "n2", "n3")
