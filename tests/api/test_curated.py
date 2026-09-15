"""큐레이션 목록 — REQ-019 (AC-058~AC-061).

**이 테스트는 좌표가 있든 없든 통과해야 한다.** 데이터셋의 좌표 확보율은 조사
진행에 따라 달라지는 값이지 계약이 아니다. 계약인 것은 이쪽이다 —

- 좌표가 없는 항목을 **목록에서 빼지 않는다**(빼면 "홍콩에 미쉐린이 몇 곳뿐"이라고
  잘못 읽힌다),
- 거리 정렬이 좌표 없는 항목 때문에 **터지지 않는다**(None 과 float 비교),
- 출처와 한계(`sources`·`known_gaps`)를 **항상 함께 준다**.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

DATA = Path(__file__).resolve().parents[2] / "seed" / "curated-places.json"
TST_LAT, TST_LNG = 22.2937, 114.1730


@pytest.fixture(scope="module")
def dataset() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))


# ── AC-058 : 목록과 출처 ──────────────────────────────────────────────────
def test_returns_the_whole_list(client: Any, dataset: dict[str, Any]) -> None:
    body = client.get("/api/curated").json()
    assert body["returned"] == len(dataset["places"])
    assert body["returned"] > 0


def test_every_entry_has_name_city_and_tier(client: Any) -> None:
    for place in client.get("/api/curated").json()["places"]:
        assert place["name"].strip()
        assert place["city"] in {"HK", "MO"}
        assert place["stars"] in {1, 2, 3}
        assert place["tier_label"].strip()


def test_sources_and_gaps_always_travel_with_the_data(client: Any) -> None:
    """출처 없는 '추천 목록'은 그냥 누군가의 취향이다.

    한계(`known_gaps`)도 같이 준다 — 빕구르망이 빠져 있고 좌표가 비어 있다는
    사실을 모르면 이 목록을 '홍콩 맛집 전부'로 읽게 된다.
    """
    body = client.get("/api/curated").json()
    assert body["sources"], "출처가 비었다"
    assert all(s.get("url") for s in body["sources"])
    assert body["known_gaps"], "알려진 한계가 비었다"
    assert body["retrieved_at"], "조회 시점이 없다"


# ── AC-059 : 필터 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("city", ["HK", "MO"])
def test_city_filter(client: Any, city: str) -> None:
    places = client.get("/api/curated", params={"city": city}).json()["places"]
    assert places
    assert {p["city"] for p in places} == {city}


def test_min_stars_filter(client: Any) -> None:
    places = client.get("/api/curated", params={"min_stars": 3}).json()["places"]
    assert places
    assert all(p["stars"] >= 3 for p in places)


def test_unknown_city_is_rejected(client: Any) -> None:
    assert client.get("/api/curated", params={"city": "JP"}).status_code == 422


@pytest.mark.parametrize("stars", [0, 4])
def test_star_filter_outside_the_scale_is_rejected(client: Any, stars: int) -> None:
    assert client.get("/api/curated", params={"min_stars": stars}).status_code == 422


# ── AC-060 : 거리 정렬이 좌표 없는 항목을 견딘다 ─────────────────────────
def test_distance_sort_keeps_entries_without_coordinates(client: Any) -> None:
    """좌표 없는 항목을 **빼지 않는다.**

    빼면 목록이 조용히 줄어 사용자는 그만큼만 존재한다고 믿는다. 뒤로 미루되 남긴다.
    """
    everything = client.get("/api/curated").json()["returned"]
    sorted_body = client.get("/api/curated", params={"lat": TST_LAT, "lng": TST_LNG}).json()
    assert sorted_body["returned"] == everything


def test_distances_ascend_and_unlocated_sink_to_the_bottom(client: Any) -> None:
    places = client.get("/api/curated", params={"lat": TST_LAT, "lng": TST_LNG}).json()["places"]
    located = [p["distance_m"] for p in places if p["distance_m"] is not None]
    assert located == sorted(located), "거리순이 아니다"

    seen_unlocated = False
    for place in places:
        if place["distance_m"] is None:
            seen_unlocated = True
        else:
            assert not seen_unlocated, "좌표 없는 항목 뒤에 좌표 있는 항목이 나왔다"


def test_half_an_origin_is_rejected(client: Any) -> None:
    """위도만 주면 조용히 무시하지 않고 거절한다 — 사용자는 거리순으로 보고 있다고
    믿는데 실제로는 아닌 상태가 가장 나쁘다."""
    assert client.get("/api/curated", params={"lat": TST_LAT}).status_code == 422
    assert client.get("/api/curated", params={"lng": TST_LNG}).status_code == 422


def test_coordinates_when_present_are_inside_hong_kong_or_macau(client: Any) -> None:
    """좌표가 채워졌다면 **반드시** 홍콩·마카오 범위 안이어야 한다.

    이 데이터셋의 좌표는 조사로 채워지는 중이라 언제든 늘어난다. 범위 밖 좌표는
    조사 실수의 신호이므로 여기서 막는다(바다 한가운데 핀을 막는 마지막 그물).
    """
    box = {"HK": (22.15, 22.58, 113.82, 114.44), "MO": (22.10, 22.22, 113.52, 113.60)}
    for place in client.get("/api/curated").json()["places"]:
        if place["lat"] is None:
            continue
        south, north, west, east = box[place["city"]]
        assert south <= place["lat"] <= north, f'{place["name"]} 위도 범위 밖'
        assert west <= place["lng"] <= east, f'{place["name"]} 경도 범위 밖'


def test_a_located_entry_has_its_confidence_recorded(client: Any) -> None:
    """좌표가 있으면 **어떻게 알아냈는지**도 있어야 한다.

    근거 없는 좌표는 나중에 누구도 검증하지 못한다.
    """
    for place in client.get("/api/curated").json()["places"]:
        if place["lat"] is not None:
            assert place.get("coord_confidence"), f'{place["name"]} 좌표 근거 없음'


# ── AC-061 : 데이터셋 자체의 무결성 ──────────────────────────────────────
def test_counts_match_the_actual_rows(dataset: dict[str, Any]) -> None:
    places = dataset["places"]
    counts = dataset["counts"]
    assert counts["total"] == len(places)
    assert counts["hong_kong"] == sum(1 for p in places if p["city"] == "HK")
    assert counts["macau"] == sum(1 for p in places if p["city"] == "MO")
    for key, star in (("three_star", 3), ("two_star", 2), ("one_star", 1)):
        assert counts[key] == sum(1 for p in places if p["stars"] == star)


def test_totals_match_the_official_michelin_counts(dataset: dict[str, Any]) -> None:
    """미쉐린 공식 집계는 총 98곳(홍콩 77 · 마카오 21)이다.

    명단은 2차 출처에서 옮긴 것이라 **이 대조가 유일한 완전성 근거다.** 어긋나면
    빠졌거나 중복된 것이다(실제로 처음엔 마카오가 20곳이어서 빠진 한 곳을 찾아냈다).
    """
    counts = dataset["counts"]
    assert counts["hong_kong"] == 77
    assert counts["macau"] == 21
    assert counts["total"] == 98


def test_with_coordinates_count_matches_reality(dataset: dict[str, Any]) -> None:
    """집계가 실제와 어긋나면 어느 쪽을 믿어야 할지 알 수 없다."""
    actual = sum(1 for p in dataset["places"] if p.get("lat") is not None)
    assert dataset["counts"]["with_coordinates"] == actual


def test_no_duplicate_entries(dataset: dict[str, Any]) -> None:
    """같은 도시에 같은 이름이 둘이면 옮기다 중복된 것이다.

    (`8½ Otto e Mezzo – Bombana` 는 홍콩과 마카오에 각각 있다 — 도시까지 묶어야
    진짜 중복만 잡힌다.)
    """
    keys = [(p["city"], p["name"]) for p in dataset["places"]]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"중복 항목: {duplicates}"
