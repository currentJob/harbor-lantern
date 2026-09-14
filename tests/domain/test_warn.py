"""경고 생성기 — AC-023 · AC-024 (설계서 §6.10 · §12 F9).

경고는 **없어야 할 때 없는 것**이 더 중요하다. 거짓 경고 하나가 진짜 경고 전부를
무시하게 만든다 — 그래서 "경고 0건" 테스트가 "경고 1건" 테스트보다 많다.
"""

from __future__ import annotations

from harbor_lantern.domain.hours import parse_closed, parse_hours
from harbor_lantern.domain.models import ClosedSpec, HoursSpec, ScheduledSpot
from harbor_lantern.domain.warn import KIND_CLOSED_DAY, KIND_CLOSED_ON_ARRIVAL, build_warnings

MONDAY = 0
SATURDAY = 5


def scheduled(spot_id: str, eta_min: int, dwell: int = 60) -> ScheduledSpot:
    return ScheduledSpot(
        spot_id=spot_id,
        eta_min=eta_min,
        depart_min=eta_min + dwell,
        dwell_minutes=dwell,
        dwell_source="time_band",
    )


class TestClosedOnArrival:
    """AC-023 — 도착 예상시각이 마감 이후면 경고 1건, 영업시간 내면 0건."""

    def test_ac023_arrival_after_closing_makes_one_warning(self) -> None:
        hours = {"manmo": parse_hours("매일 08:00–18:00")}
        warnings = build_warnings([scheduled("manmo", 19 * 60)], hours, {}, MONDAY)
        assert len(warnings) == 1
        assert warnings[0].kind == KIND_CLOSED_ON_ARRIVAL
        assert warnings[0].spot_id == "manmo"
        assert warnings[0].eta_local == "19:00"
        assert "08:00" in warnings[0].message and "18:00" in warnings[0].message

    def test_ac023_arrival_within_opening_hours_makes_no_warning(self) -> None:
        hours = {"manmo": parse_hours("매일 08:00–18:00")}
        assert build_warnings([scheduled("manmo", 12 * 60)], hours, {}, MONDAY) == []

    def test_ac023_arrival_before_opening_is_also_outside_the_window(self) -> None:
        hours = {"manmo": parse_hours("매일 08:00–18:00")}
        warnings = build_warnings([scheduled("manmo", 7 * 60)], hours, {}, MONDAY)
        assert [w.kind for w in warnings] == [KIND_CLOSED_ON_ARRIVAL]

    def test_ac023_exactly_at_closing_time_is_not_a_warning(self) -> None:
        """경계 규약: 개방 구간은 양끝 포함. AC-023 은 '마감시각 **이후**'라고 적었다."""
        hours = {"manmo": parse_hours("매일 08:00–18:00")}
        assert build_warnings([scheduled("manmo", 18 * 60)], hours, {}, MONDAY) == []

    def test_open_24h_never_warns(self) -> None:
        hours = {"clock": parse_hours("야외 상시")}
        assert build_warnings([scheduled("clock", 3 * 60)], hours, {}, MONDAY) == []

    def test_weekday_table_decides_per_visit_day(self) -> None:
        hours = {"cable": parse_hours("평일 10:00–18:00 / 주말·공휴일 09:00–18:30")}
        assert len(build_warnings([scheduled("cable", 9 * 60 + 30)], hours, {}, MONDAY)) == 1
        assert build_warnings([scheduled("cable", 9 * 60 + 30)], hours, {}, SATURDAY) == []

    def test_after_midnight_eta_is_judged_on_the_wrapped_minute(self) -> None:
        """25:00(=1500분) 도착은 01:00 판정이다 (§6.10)."""
        hours = {"market": parse_hours("매일 대략 18:00–24:00")}
        warnings = build_warnings([scheduled("market", 1500)], hours, {}, MONDAY)
        assert [w.kind for w in warnings] == [KIND_CLOSED_ON_ARRIVAL]
        assert warnings[0].eta_local == "01:00"


class TestUnknownMakesNoWarning:
    """AC-024 — 파싱 실패는 경고 0건. 이 파일에서 가장 중요한 클래스다."""

    def test_ac024_unknown_hours_never_warn_whatever_the_time(self) -> None:
        hours = {"lkf": parse_hours("저녁~심야")}
        for eta in (0, 6 * 60, 12 * 60, 23 * 60, 1500):
            assert build_warnings([scheduled("lkf", eta)], hours, {}, MONDAY) == []

    def test_ac024_all_six_intentionally_unknown_seed_strings_are_silent(self) -> None:
        texts = [
            "단지 상시 · 상점 대략 11:00–20:00",
            "하행 06:00–10:00 / 상행 10:00–24:00",
            "야외 상시 / 내부 대략 10:00–17:30",
            "매일 09:00–18:00 / 채식당 11:30–16:30",
            "저녁~심야",
            "매일 20:00 시작 · 약 10~13분",
        ]
        hours = {f"s{i}": parse_hours(text) for i, text in enumerate(texts)}
        schedule = [scheduled(f"s{i}", 23 * 60) for i in range(len(texts))]
        assert build_warnings(schedule, hours, {}, MONDAY) == []

    def test_spot_missing_from_the_hours_map_is_silent(self) -> None:
        """스팟이 표에 아예 없어도 조용하다 — 없는 것과 못 읽은 것은 같은 취급이다."""
        assert build_warnings([scheduled("ghost", 23 * 60)], {}, {}, MONDAY) == []

    def test_weekday_table_without_that_day_is_silent(self) -> None:
        spec = HoursSpec(status="open_range", weekday_open={0: (600, 1080)}, pattern="P-01")
        assert build_warnings([scheduled("s", 700)], {"s": spec}, {}, 3) == []


class TestClosedDay:
    """AC-024 후단 — 휴무 요일에 방문 예정이면 경고 1건."""

    def test_ac024_visiting_on_a_closed_weekday_warns_once(self) -> None:
        closed = {"taikwun": parse_closed("일부 갤러리 월요일")}
        warnings = build_warnings([scheduled("taikwun", 11 * 60)], {}, closed, MONDAY)
        assert len(warnings) == 1
        assert warnings[0].kind == KIND_CLOSED_DAY
        assert "월요일" in warnings[0].message

    def test_ac024_other_weekdays_are_silent(self) -> None:
        closed = {"taikwun": parse_closed("일부 갤러리 월요일")}
        assert build_warnings([scheduled("taikwun", 11 * 60)], {}, closed, SATURDAY) == []

    def test_empty_closed_spec_is_silent(self) -> None:
        assert build_warnings([scheduled("s", 660)], {}, {"s": ClosedSpec()}, MONDAY) == []


class TestWarningShape:
    def test_at_most_one_warning_per_kind_per_spot(self) -> None:
        hours = {"s": parse_hours("매일 08:00–18:00")}
        closed = {"s": parse_closed("월요일")}
        warnings = build_warnings([scheduled("s", 20 * 60)], hours, closed, MONDAY)
        kinds = [w.kind for w in warnings]
        assert sorted(kinds) == sorted([KIND_CLOSED_ON_ARRIVAL, KIND_CLOSED_DAY])
        assert len(kinds) == len(set(kinds))

    def test_warnings_follow_the_schedule_order(self) -> None:
        hours = {name: parse_hours("매일 08:00–18:00") for name in ("a", "b", "c")}
        schedule = [scheduled("a", 20 * 60), scheduled("b", 12 * 60), scheduled("c", 21 * 60)]
        assert [w.spot_id for w in build_warnings(schedule, hours, {}, MONDAY)] == ["a", "c"]

    def test_ac048_same_input_gives_the_same_warnings(self) -> None:
        """서버 시계를 보지 않는다 — `visit_weekday` 는 일자 날짜에서 온다(§12 F1)."""
        hours = {"s": parse_hours("매일 08:00–18:00")}
        first = build_warnings([scheduled("s", 20 * 60)], hours, {}, MONDAY)
        second = build_warnings([scheduled("s", 20 * 60)], hours, {}, MONDAY)
        assert first == second
