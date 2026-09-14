"""체류시간·도착 예상시각 — AC-021 · AC-048(결정론) (설계서 §6.8).

`eta_min` 은 자정 기준 분이다. `HH:MM` 문자열로 검증하면 자정 넘김에서 조용히 통과하는
테스트가 된다 — 그래서 여기서는 정수만 본다.
"""

from __future__ import annotations

import pytest

from harbor_lantern.config import DEFAULT_TIME_BAND, FIXED_TIME_BAND, TIME_BANDS, TravelConfig
from harbor_lantern.domain.models import LatLng, SpotInput
from harbor_lantern.domain.timeline import build_timeline, resolve_dwell, resolve_start_min

CFG = TravelConfig()

A = LatLng(22.2937, 114.1730)
B = LatLng(22.2938, 114.1694)
C = LatLng(22.2759, 114.1455)


def spot(spot_id: str, coord: LatLng, label: str = "오전", dwell: int | None = None) -> SpotInput:
    return SpotInput(spot_id=spot_id, coord=coord, time_label=label, dwell_minutes=dwell)


class TestResolveDwell:
    """O6 · AC-021 전제 — 체류시간은 명시값 우선, 없으면 시간대 라벨 기본값."""

    def test_explicit_value_wins(self) -> None:
        assert resolve_dwell(spot("s1", A, "오전", dwell=25)) == (25, "explicit")

    def test_explicit_zero_is_not_treated_as_missing(self) -> None:
        """0 분 체류는 유효한 입력이다 — `if dwell:` 로 쓰면 여기서 조용히 60분이 된다."""
        assert resolve_dwell(spot("s1", A, "오전", dwell=0)) == (0, "explicit")

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("오전", 60),
            ("점심", 60),
            ("점심후", 75),
            ("오후", 60),
            ("일몰", 45),
            ("이른저녁", 45),
            ("저녁", 90),
            ("야경", 60),
            ("밤", 60),
        ],
    )
    def test_time_band_defaults_match_the_design_table(self, label: str, expected: int) -> None:
        assert resolve_dwell(spot("s1", A, label)) == (expected, "time_band")

    def test_fixed_time_label_uses_the_fixed_band(self) -> None:
        assert resolve_dwell(spot("s1", A, "20:00")) == (FIXED_TIME_BAND.dwell_minutes, "time_band")

    def test_unknown_label_falls_back_without_guessing(self) -> None:
        assert resolve_dwell(spot("s1", A, "새벽감성")) == (DEFAULT_TIME_BAND.dwell_minutes, "time_band")

    def test_every_seed_label_is_in_the_band_table(self, seed_document: dict) -> None:
        """시드 27건의 라벨이 전부 표에 있는가 — 하나라도 빠지면 그 스팟만 조용히 기본값이 된다."""
        labels = {s["time_label"] for day in seed_document["days"] for s in day["spots"]}
        unmapped = {label for label in labels if label not in TIME_BANDS}
        assert unmapped == {"20:00"}  # 고정시각 라벨 하나뿐 (심포니 오브 라이트)


class TestResolveStartMin:
    def test_start_minutes_match_the_design_table(self) -> None:
        assert resolve_start_min("오전") == 9 * 60
        assert resolve_start_min("오후") == 14 * 60
        assert resolve_start_min("점심후") == 13 * 60 + 30

    def test_fixed_label_starts_at_that_time(self) -> None:
        assert resolve_start_min("20:00") == 20 * 60

    def test_seed_day_start_local_matches_first_spot_label(self, seed_document: dict) -> None:
        """§5.3 의 유도 결과(Day1 14:00 · Day2~4 09:00)를 재현하는지."""
        derived = [resolve_start_min(day["spots"][0]["time_label"]) for day in seed_document["days"]]
        assert derived == [14 * 60, 9 * 60, 9 * 60, 9 * 60]


class TestBuildTimeline:
    """AC-021 — `시작시각 + Σ(앞 스팟 체류 + 구간 이동)`."""

    def test_ac021_eta_is_the_running_sum(self) -> None:
        spots = [spot("s1", A, dwell=30), spot("s2", B, dwell=45), spot("s3", C, dwell=20)]
        scheduled, legs = build_timeline(540, spots, CFG)

        assert [item.spot_id for item in scheduled] == ["s1", "s2", "s3"]
        assert scheduled[0].eta_min == 540
        assert scheduled[0].depart_min == 570
        assert scheduled[1].eta_min == 570 + legs[0].minutes  # type: ignore[union-attr]
        assert scheduled[1].depart_min == scheduled[1].eta_min + 45
        assert scheduled[2].eta_min == scheduled[1].depart_min + legs[1].minutes  # type: ignore[union-attr]

    def test_ac021_manual_arithmetic_matches(self) -> None:
        """공식을 손으로 한 번 더 계산한다 — 구현을 베끼지 않는 검산."""
        spots = [spot("s1", A, dwell=30), spot("s2", B, dwell=45), spot("s3", C, dwell=20)]
        scheduled, legs = build_timeline(540, spots, CFG)
        expected = 540 + 30 + legs[0].minutes + 45 + legs[1].minutes  # type: ignore[union-attr]
        assert scheduled[2].eta_min == expected

    def test_legs_are_parallel_to_spots_with_trailing_none(self) -> None:
        spots = [spot("s1", A), spot("s2", B), spot("s3", C)]
        scheduled, legs = build_timeline(540, spots, CFG)
        assert len(legs) == len(scheduled) == 3
        assert legs[-1] is None
        assert all(item is not None for item in legs[:-1])

    def test_ac021_reordering_recomputes_arrival_times(self) -> None:
        """순서를 바꾸면 도착 예상시각이 재계산된다 (AC-021 후단)."""
        forward = [spot("s1", A, dwell=30), spot("s2", C, dwell=30), spot("s3", B, dwell=30)]
        reversed_order = [forward[0], forward[2], forward[1]]

        eta_forward = {item.spot_id: item.eta_min for item in build_timeline(540, forward, CFG)[0]}
        eta_reversed = {item.spot_id: item.eta_min for item in build_timeline(540, reversed_order, CFG)[0]}

        assert eta_forward != eta_reversed
        assert eta_forward["s1"] == eta_reversed["s1"] == 540  # 첫 스팟만 같다

    def test_ac048_same_input_gives_the_same_timeline_every_time(self) -> None:
        """서버 시계와 무관하다 — 입력이 같으면 결과가 같다(NFR-013 · §12 F1)."""
        spots = [spot("s1", A, dwell=30), spot("s2", B, dwell=45)]
        assert build_timeline(540, spots, CFG) == build_timeline(540, spots, CFG)

    def test_past_midnight_eta_keeps_counting_up(self) -> None:
        """밤 일정은 1440 을 넘는다. `HH:MM` 로 감아 버리면 다음날이 사라진다(§6.2)."""
        spots = [spot("s1", A, "밤", dwell=120), spot("s2", B, "밤", dwell=60)]
        scheduled, _ = build_timeline(23 * 60, spots, CFG)
        assert scheduled[0].depart_min == 23 * 60 + 120  # 1500 = 다음날 01:00
        assert scheduled[1].eta_min > 1440

    def test_empty_day(self) -> None:
        assert build_timeline(540, [], CFG) == ([], [])

    def test_fixed_time_spot_is_not_silently_shifted_to_its_fixed_time(self) -> None:
        """타임라인은 고정시각을 **맞춰 주지 않는다** — 맞춰 주면 충돌이 영원히 0건이다(§6.12).

        앞 일정이 늦어지면 늦어진 대로, 이르면 이른 대로 ETA 에 남는다.
        그 사실을 `conflict.find_conflicts` 가 읽어 침범을 검출한다.
        """
        late = build_timeline(540, [spot("prev", A, dwell=700), spot("symphony", B, "20:00")], CFG)[0]
        assert late[1].eta_min > 20 * 60  # 밀린 사실이 그대로 남는다

        early = build_timeline(540, [spot("prev", A, dwell=60), spot("symphony", B, "20:00")], CFG)[0]
        assert early[1].eta_min < 20 * 60  # 이른 것도 그대로 남는다
