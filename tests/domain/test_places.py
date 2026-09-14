"""근처 장소 정규화·중복 제거·정렬 — REQ-017 (AC-051 · AC-056).

이 모듈의 실패 방식은 하나다: **버려야 할 것을 남기거나, 남겨야 할 것을 버리는 것.**
OSM 은 누구나 쓰는 데이터라 이름이 없거나, 같은 가게가 노드와 웨이로 두 번 들어오거나,
우리가 묻지도 않은 태그가 딸려 온다. 여기서 거르지 않으면 화면에 "무명 식당"이 뜬다.
"""

from __future__ import annotations

import pytest

from harbor_lantern.config import nearby_tag_index
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.places import (
    Place,
    dedupe_places,
    element_to_place,
    normalize_name,
    normalize_places,
    sort_by_distance,
)

TAGS = nearby_tag_index(("restaurant", "cafe", "fast_food"))
TST = LatLng(22.2937, 114.1730)


def node(osm_id: int, name: str | None, lat: float, lng: float, **tags: str) -> dict:
    element: dict = {"type": "node", "id": osm_id, "lat": lat, "lng": lng,
                     "tags": dict(tags)}
    element["lat"], element["lon"] = lat, lng
    if name is not None:
        element["tags"]["name"] = name
    return element


# ── element_to_place : 무엇을 버리는가 (AC-056) ───────────────────────────
def test_a_named_restaurant_becomes_a_place() -> None:
    place = element_to_place(node(1, "국수집", 22.294, 114.174, amenity="restaurant"), TAGS)
    assert place is not None
    assert place.name == "국수집"
    assert place.category == "restaurant"
    assert place.key == "node/1"


def test_an_unnamed_element_is_dropped() -> None:
    """지도에 "무명 식당"을 띄울 수는 없다."""
    assert element_to_place(node(1, None, 22.294, 114.174, amenity="restaurant"), TAGS) is None


def test_a_blank_name_is_dropped() -> None:
    assert element_to_place(node(1, "   ", 22.294, 114.174, amenity="restaurant"), TAGS) is None


def test_a_tag_we_did_not_ask_for_is_dropped() -> None:
    """주유소를 맛집이라고 보여 주면 안 된다."""
    assert element_to_place(node(1, "주유소", 22.294, 114.174, amenity="fuel"), TAGS) is None


def test_an_element_without_coordinates_is_dropped() -> None:
    broken = {"type": "node", "id": 1, "tags": {"name": "좌표 없음", "amenity": "cafe"}}
    assert element_to_place(broken, TAGS) is None


def test_a_way_uses_its_center_point() -> None:
    """건물(way)은 좌표가 아니라 `center` 를 가진다 — Overpass `out center` 의 산물."""
    way = {"type": "way", "id": 77, "center": {"lat": 22.295, "lon": 114.175},
           "tags": {"name": "큰 식당", "amenity": "restaurant"}}
    place = element_to_place(way, TAGS)
    assert place is not None
    assert place.coord == LatLng(22.295, 114.175)


def test_a_boolean_id_is_not_an_id() -> None:
    """파이썬에서 `True` 는 `int` 다. 타입 검사만으로는 안 걸린다."""
    weird = {"type": "node", "id": True, "lat": 22.29, "lon": 114.17,
             "tags": {"name": "수상한 곳", "amenity": "cafe"}}
    assert element_to_place(weird, TAGS) is None


def test_normalize_places_skips_the_unusable_without_raising() -> None:
    """원소 하나가 이상하다고 전체 조회가 실패하면 안 된다."""
    elements = [
        node(1, "좋은 집", 22.294, 114.174, amenity="restaurant"),
        node(2, None, 22.295, 114.175, amenity="restaurant"),  # 이름 없음
        "문자열이 왜 여기 있지",                                    # 아예 잘못된 타입
        node(3, "카페", 22.296, 114.176, amenity="cafe"),
    ]
    places = normalize_places(elements, TAGS)
    assert [place.name for place in places] == ["좋은 집", "카페"]


# ── normalize_name : 중복 판정의 기준 ─────────────────────────────────────
@pytest.mark.parametrize(("left", "right"), [
    ("Tim Ho Wan", "tim ho wan"),
    ("팀호완  센트럴", "팀호완 센트럴"),
    (" 앞뒤 공백 ", "앞뒤 공백"),
])
def test_names_that_should_match(left: str, right: str) -> None:
    assert normalize_name(left) == normalize_name(right)


def test_names_that_must_not_match() -> None:
    """더 영리하게 만들면(괄호 제거·약칭 통일) 서로 다른 가게가 합쳐지기 시작한다."""
    assert normalize_name("팀호완 센트럴") != normalize_name("팀호완 삼수이포")


# ── dedupe_places : 같은 가게가 두 번 오는 경우 (AC-056) ──────────────────
def make(osm_type: str, osm_id: int, name: str, lat: float, lng: float) -> Place:
    return Place(osm_type=osm_type, osm_id=osm_id, name=name,
                 coord=LatLng(lat, lng), category="restaurant")


def test_the_same_shop_as_node_and_way_collapses_to_one() -> None:
    places = [make("way", 20, "같은 집", 22.29371, 114.17301),
              make("node", 10, "같은 집", 22.29370, 114.17300)]
    result = dedupe_places(places)
    assert len(result) == 1
    assert result[0].osm_type == "node", "노드를 남겨야 한다(더 구체적인 지점)"


def test_same_name_far_apart_is_a_different_branch() -> None:
    """이름만 보고 합치면 체인점 지점이 전부 하나로 사라진다."""
    places = [make("node", 10, "팀호완", 22.2937, 114.1730),
              make("node", 11, "팀호완", 22.3300, 114.1600)]  # 약 4km
    assert len(dedupe_places(places)) == 2


def test_different_names_at_the_same_spot_both_survive() -> None:
    """한 건물에 식당이 둘 있을 수 있다."""
    places = [make("node", 10, "1층 식당", 22.2937, 114.1730),
              make("node", 11, "2층 식당", 22.2937, 114.1730)]
    assert len(dedupe_places(places)) == 2


def test_dedupe_is_deterministic_regardless_of_input_order() -> None:
    """입력 순서가 결과를 바꾸면 같은 질의가 매번 다른 답을 준다."""
    a = make("node", 10, "같은 집", 22.29370, 114.17300)
    b = make("way", 20, "같은 집", 22.29371, 114.17301)
    assert dedupe_places([a, b]) == dedupe_places([b, a])


# ── sort_by_distance (AC-051) ─────────────────────────────────────────────
def test_sorted_by_distance_ascending() -> None:
    far = make("node", 1, "먼 집", 22.3100, 114.1900)
    near = make("node", 2, "가까운 집", 22.2938, 114.1731)
    mid = make("node", 3, "중간 집", 22.2970, 114.1760)
    ordered = sort_by_distance([far, near, mid], TST)
    assert [place.name for place, _ in ordered] == ["가까운 집", "중간 집", "먼 집"]
    distances = [distance for _, distance in ordered]
    assert distances == sorted(distances)


def test_ties_break_deterministically() -> None:
    """거리가 같을 때 순서가 흔들리면 화면이 요청마다 다르게 그려진다."""
    left = make("node", 2, "가", 22.2938, 114.1731)
    right = make("node", 1, "나", 22.2938, 114.1731)
    first = [place.key for place, _ in sort_by_distance([left, right], TST)]
    second = [place.key for place, _ in sort_by_distance([right, left], TST)]
    assert first == second


def test_distance_is_measured_from_the_given_origin() -> None:
    place = make("node", 1, "집", 22.2937, 114.1730)
    at_the_spot = sort_by_distance([place], TST)[0][1]
    far_away = sort_by_distance([place], LatLng(22.3300, 114.1600))[0][1]
    assert at_the_spot < 5
    assert far_away > 3000
