"""거리 계산·표기 — AC-019 · AC-032 · AC-033(계산 부분) · AC-034 (설계서 §6.3).

`haversine_m` 의 기대값을 손으로 적으면 그건 "구현이 내놓은 값"을 베낀 것에 불과하다.
그래서 두 가지 **독립 기준**으로 잰다:

1. 해석적으로 아는 값 — 적도에서 경도 1도, 사분원 길이.
2. 다른 공식 — 구면 코사인 법칙(같은 구, 다른 식). 하버사인의 구현 실수는 잡히고
   구 근사 자체의 오차는 공유되므로 1% 판정에 적합하다(AC-019).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from harbor_lantern.domain.geo import (
    EARTH_RADIUS_M,
    directions_url,
    format_distance,
    haversine_m,
    path_length_m,
)
from harbor_lantern.domain.models import LatLng

# 시드에 실제로 들어 있는 좌표 (reference/original-static-page.html 원본 값).
STAR_AVENUE = LatLng(22.2937, 114.1730)
CLOCK_TOWER = LatLng(22.2938, 114.1694)
VICTORIA_PEAK = LatLng(22.2759, 114.1455)
TAI_O = LatLng(22.2551, 113.8630)


def _law_of_cosines_m(a: LatLng, b: LatLng) -> float:
    """구면 코사인 법칙 — 하버사인과 **독립인** 두 번째 구현 (같은 반지름)."""
    rad = math.pi / 180.0
    lat1, lat2 = a.lat * rad, b.lat * rad
    d_lng = (b.lng - a.lng) * rad
    cosine = math.sin(lat1) * math.sin(lat2) + math.cos(lat1) * math.cos(lat2) * math.cos(d_lng)
    return EARTH_RADIUS_M * math.acos(min(1.0, max(-1.0, cosine)))


class TestHaversine:
    """AC-019 — 하버사인 거리, 기대값과 오차 1% 이내."""

    def test_ac019_zero_distance_for_identical_points(self) -> None:
        assert haversine_m(STAR_AVENUE, STAR_AVENUE) == 0.0

    def test_ac019_symmetric(self) -> None:
        assert haversine_m(STAR_AVENUE, TAI_O) == pytest.approx(haversine_m(TAI_O, STAR_AVENUE))

    def test_ac019_one_degree_of_longitude_at_equator(self) -> None:
        """해석값: R × π/180 = 111,194.9 m."""
        expected = EARTH_RADIUS_M * math.pi / 180.0
        measured = haversine_m(LatLng(0.0, 0.0), LatLng(0.0, 1.0))
        assert measured == pytest.approx(expected, rel=0.01)

    def test_ac019_quarter_of_the_equator(self) -> None:
        """해석값: R × π/2 = 10,007,543 m."""
        expected = EARTH_RADIUS_M * math.pi / 2.0
        measured = haversine_m(LatLng(0.0, 0.0), LatLng(0.0, 90.0))
        assert measured == pytest.approx(expected, rel=0.01)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            (STAR_AVENUE, CLOCK_TOWER),
            (STAR_AVENUE, VICTORIA_PEAK),
            (STAR_AVENUE, TAI_O),
            (VICTORIA_PEAK, TAI_O),
        ],
    )
    def test_ac019_matches_independent_formula_within_one_percent(self, a: LatLng, b: LatLng) -> None:
        assert haversine_m(a, b) == pytest.approx(_law_of_cosines_m(a, b), rel=0.01)

    def test_ac019_neighbouring_seed_spots_are_a_few_hundred_metres_apart(self) -> None:
        """스타 애비뉴 ↔ 시계탑은 걸어서 갈 거리다 — 자릿수 실수(도↔라디안)를 잡는다."""
        assert 300.0 < haversine_m(STAR_AVENUE, CLOCK_TOWER) < 500.0

    def test_ac019_path_length_is_the_sum_of_legs(self) -> None:
        """일자 총 거리 == 구간 거리의 합 (AC-019 후단)."""
        route = [STAR_AVENUE, CLOCK_TOWER, VICTORIA_PEAK, TAI_O]
        expected = sum(haversine_m(route[i], route[i + 1]) for i in range(len(route) - 1))
        assert path_length_m(route) == pytest.approx(expected)

    def test_ac019_path_length_of_single_point_is_zero(self) -> None:
        assert path_length_m([STAR_AVENUE]) == 0.0
        assert path_length_m([]) == 0.0


class TestFormatDistance:
    """AC-032 — 1000m 미만은 10m 단위 `m`, 이상은 `km`. 참조 HTML `fmt()` 와 같은 값."""

    @pytest.mark.parametrize(
        ("meters", "expected"),
        [
            (0.0, "0m"),
            (4.0, "0m"),
            (5.0, "10m"),  # JS Math.round(0.5) = 1 — 파이썬 내장 round 였다면 '0m'
            (12.0, "10m"),
            (128.0, "130m"),
            (994.0, "990m"),
            (995.0, "1000m"),
            (999.9, "1000m"),
            (1000.0, "1.0km"),
            (1249.0, "1.2km"),
            (1250.0, "1.3km"),  # JS (1.25).toFixed(1) = '1.3' — f"{1.25:.1f}" 였다면 '1.2'
            (9999.0, "10.0km"),
            (10000.0, "10km"),
            (10500.0, "11km"),  # JS (10.5).toFixed(0) = '11'
            (12345.0, "12km"),
        ],
    )
    def test_ac032_matches_reference_html_rule(self, meters: float, expected: str) -> None:
        assert format_distance(meters) == expected

    def test_ac032_boundary_switches_unit_at_exactly_1000m(self) -> None:
        assert format_distance(999.4).endswith("m")
        assert not format_distance(999.4).endswith("km")
        assert format_distance(1000.0).endswith("km")


class TestSortByDistance:
    """AC-033(계산 부분) — 현재 위치로부터의 거리 오름차순 정렬."""

    def test_ac033_sorting_key_is_haversine_distance(self) -> None:
        me = LatLng(22.2940, 114.1700)  # 침사추이 부근
        spots = [("taio", TAI_O), ("peak", VICTORIA_PEAK), ("clock", CLOCK_TOWER)]
        ordered = [name for name, _ in sorted(spots, key=lambda item: haversine_m(me, item[1]))]
        assert ordered == ["clock", "peak", "taio"]

    def test_ac033_ordering_is_stable_for_equal_distances(self) -> None:
        """같은 거리면 원래 순서를 지킨다 — 화면이 이유 없이 흔들리지 않는다."""
        me = LatLng(22.30, 114.17)
        same = [("a", CLOCK_TOWER), ("b", CLOCK_TOWER)]
        assert [n for n, _ in sorted(same, key=lambda item: haversine_m(me, item[1]))] == ["a", "b"]


class TestDirectionsUrl:
    """AC-034 — 구글맵 딥링크 형식과 좌표 일치."""

    def test_ac034_format_and_coordinates(self) -> None:
        url = directions_url(22.2937, 114.1730)
        assert url == "https://www.google.com/maps/dir/?api=1&destination=22.2937,114.173"

    def test_ac034_trailing_zero_is_dropped_like_javascript(self) -> None:
        """참조 HTML 은 `sp.lat + "," + sp.lng` 로 만든다 — JS 는 `114.1730` 을 `114.173` 으로 낸다."""
        assert directions_url(22.2937, 114.1730).endswith("=22.2937,114.173")

    def test_ac034_every_seed_spot_gets_its_own_coordinates(self, seed_document: dict[str, Any]) -> None:
        for day in seed_document["days"]:
            for spot in day["spots"]:
                url = directions_url(spot["lat"], spot["lng"])
                assert url.startswith("https://www.google.com/maps/dir/?api=1&destination=")
                latitude, longitude = url.rsplit("=", 1)[1].split(",")
                assert float(latitude) == spot["lat"]
                assert float(longitude) == spot["lng"]
