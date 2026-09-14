"""이동시간 엔진 — AC-019(총 거리) · AC-020 (설계서 §6.7 · §12 F8).

**AC-020 의 비례성은 도보 모드로 검증한다.** 대중교통에는 환승 여유라는 덧셈 항이
있어서 우회계수를 2배로 해도 `minutes` 가 2배가 되지 않는다 — 그걸 모르고 대중교통으로
검증하면 멀쩡한 구현이 실패한다(설계서 §12 F8).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from harbor_lantern.config import TravelConfig
from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.travel import day_totals, leg

CFG = TravelConfig()

NEAR_A = LatLng(22.2937, 114.1730)  # 스타 애비뉴
NEAR_B = LatLng(22.2938, 114.1694)  # 시계탑 — 약 370m (도보)
FAR = LatLng(22.2551, 113.8630)  # 타이오 — 약 32km (대중교통)


class TestModeSelection:
    def test_short_leg_is_walk(self) -> None:
        result = leg(NEAR_A, NEAR_B, CFG)
        assert result.distance_m <= CFG.mode_threshold_m
        assert result.mode == "walk"
        assert result.overhead_seconds == 0

    def test_long_leg_is_transit_with_fixed_overhead(self) -> None:
        result = leg(NEAR_A, FAR, CFG)
        assert result.distance_m > CFG.mode_threshold_m
        assert result.mode == "transit"
        assert result.overhead_seconds == CFG.transit_overhead_min * 60

    def test_mode_threshold_boundary_is_inclusive_for_walk(self) -> None:
        """정확히 임계 거리면 도보다 (`d <= threshold`). 경계 규약을 고정한다."""
        cfg = replace(CFG, mode_threshold_m=haversine_m(NEAR_A, NEAR_B))
        assert leg(NEAR_A, NEAR_B, cfg).mode == "walk"


class TestEstimatedFlagAndDeterminism:
    def test_ac020_estimated_flag_is_always_true(self) -> None:
        """실제 경로탐색이 아니다(A8 · Won't) — 화면과 API 가 그렇게 말해야 한다."""
        assert leg(NEAR_A, NEAR_B, CFG).estimated is True
        assert leg(NEAR_A, FAR, CFG).estimated is True

    def test_ac020_same_input_same_output(self) -> None:
        assert leg(NEAR_A, FAR, CFG) == leg(NEAR_A, FAR, CFG)


class TestProportionality:
    """AC-020 — 우회계수를 2배로 하면 이동시간이 비례해 증가한다 (도보 모드)."""

    def test_ac020_doubling_walk_detour_doubles_travel_seconds(self) -> None:
        base = leg(NEAR_A, NEAR_B, CFG)
        doubled = leg(NEAR_A, NEAR_B, replace(CFG, detour_factor_walk=CFG.detour_factor_walk * 2))
        # 초 단위 반올림 때문에 최대 1초 차이는 남는다 — 그 이상이면 비례가 아니다.
        assert abs(doubled.travel_seconds - 2 * base.travel_seconds) <= 1
        assert doubled.overhead_seconds == 0 == base.overhead_seconds

    def test_ac020_halving_walk_speed_doubles_travel_seconds(self) -> None:
        base = leg(NEAR_A, NEAR_B, CFG)
        slower = leg(NEAR_A, NEAR_B, replace(CFG, walk_speed_kmh=CFG.walk_speed_kmh / 2))
        assert abs(slower.travel_seconds - 2 * base.travel_seconds) <= 1

    def test_ac020_transit_minutes_do_not_double_because_overhead_is_additive(self) -> None:
        """§12 F8 을 **테스트로 박아 둔다** — 이 사실을 잊으면 다음 사람이 도보 검증을 지운다."""
        base = leg(NEAR_A, FAR, CFG)
        doubled = leg(NEAR_A, FAR, replace(CFG, detour_factor_transit=CFG.detour_factor_transit * 2))
        assert abs(doubled.travel_seconds - 2 * base.travel_seconds) <= 1
        assert doubled.minutes < 2 * base.minutes  # 환승 여유는 두 배가 되지 않는다


class TestMinutes:
    def test_zero_distance_gives_zero_minutes(self) -> None:
        """같은 좌표에 최소 1분을 붙이면 하루가 이유 없이 밀린다."""
        result = leg(NEAR_A, NEAR_A, CFG)
        assert result.distance_m == 0.0
        assert result.minutes == 0

    def test_tiny_distance_gets_the_minimum_leg_minutes(self) -> None:
        close_by = LatLng(NEAR_A.lat + 0.00001, NEAR_A.lng)  # 약 1m
        result = leg(NEAR_A, close_by, CFG)
        assert 0 < result.distance_m < 5
        assert result.minutes == CFG.min_leg_minutes

    def test_minutes_uses_half_up_rounding(self) -> None:
        """`(travel+overhead)/60` 이 정확히 x.5 일 때 위로 올린다 (내장 round 는 은행가 반올림)."""
        # 도보 90초 = 1.5분 → 2분. 90초가 나오도록 속도를 맞춘다.
        distance = haversine_m(NEAR_A, NEAR_B)
        speed_kmh = distance * CFG.detour_factor_walk / 90.0 * 3.6
        result = leg(NEAR_A, NEAR_B, replace(CFG, walk_speed_kmh=speed_kmh))
        assert result.travel_seconds == 90
        assert result.minutes == 2


class TestDayTotals:
    """AC-019 후단 — 일자 총 거리는 구간 거리의 합과 같다."""

    def test_ac019_totals_are_sums(self) -> None:
        legs = [leg(NEAR_A, NEAR_B, CFG), leg(NEAR_B, FAR, CFG)]
        distance, minutes = day_totals(legs)
        assert distance == pytest.approx(sum(item.distance_m for item in legs))
        assert minutes == sum(item.minutes for item in legs)

    def test_totals_skip_the_trailing_none_from_build_timeline(self) -> None:
        legs = [leg(NEAR_A, NEAR_B, CFG), None]
        distance, minutes = day_totals(legs)
        assert distance == pytest.approx(legs[0].distance_m)  # type: ignore[union-attr]
        assert minutes == legs[0].minutes  # type: ignore[union-attr]

    def test_empty_day_totals_are_zero(self) -> None:
        assert day_totals([]) == (0, 0)
