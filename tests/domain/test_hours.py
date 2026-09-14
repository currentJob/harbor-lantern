"""영업시간 파서 — AC-022 · AC-024 (설계서 §6.9 · R1 · §12 F9).

이 파일의 중심은 **시드 27건 전수 표**다. 시드의 `hours_text`·`closed_text` 가 이
파서의 실제 코퍼스이고, 파서의 유일한 실패 방식은 "너무 잘 읽으려 애쓰다 거짓 경고를
만드는 것"이다. 그래서 27건이 각각 어떤 status·pattern 으로 떨어지는지를 여기서
**전부 고정**한다 — 규칙을 하나 손대면 어느 스팟이 움직이는지가 즉시 보인다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from harbor_lantern.domain.hours import is_open_at, parse_closed, parse_hours
from harbor_lantern.domain.models import HoursSpec

# 설계서 §6.9 의 기대 분포를 스팟 단위로 편 표.
# (status, pattern, open_min, close_min, approximate, crosses_midnight)
SEED_EXPECTATION: dict[str, tuple[str, str, int | None, int | None, bool, bool]] = {
    # ── Day 1 ────────────────────────────────────────────────────────────
    "스타 애비뉴": ("open_24h", "P-05", 0, 1440, False, False),
    "시계탑": ("open_24h", "P-05", 0, 1440, False, False),
    "스타페리": ("open_range", "P-04", 390, 1410, True, False),
    "하버시티 / K11 MUSEA": ("open_range", "P-04", 600, 1320, True, False),
    "심포니 오브 라이트": ("unknown", "P-06", None, None, False, False),
    "템플 스트리트 야시장": ("open_range", "P-04", 1080, 1440, True, False),
    # ── Day 2 ────────────────────────────────────────────────────────────
    "만모사원": ("open_range", "P-04", 480, 1080, False, False),
    "PMQ": ("unknown", "P-03", None, None, False, False),
    "타이쿤": ("open_range", "P-04", 600, 1380, False, False),
    "미드레벨 에스컬레이터": ("unknown", "P-03", None, None, False, False),
    "스탠리 마켓": ("open_range", "P-04", 600, 1080, True, False),
    "리펄스 베이": ("open_24h", "P-05", 0, 1440, False, False),
    "피크트램 승차": ("open_range", "P-04", 450, 1380, False, False),
    "빅토리아 피크": ("open_range", "P-04", 510, 1320, False, False),
    "란콰이퐁 / 소호": ("unknown", "P-07", None, None, False, False),
    # ── Day 3 ────────────────────────────────────────────────────────────
    "옹핑360 케이블카": ("open_range", "P-01", None, None, False, False),
    "천단대불": ("unknown", "P-03", None, None, False, False),
    "포린사원": ("unknown", "P-03", None, None, False, False),
    "옹핑빌리지": ("open_range", "P-04", 600, 1080, True, False),
    "타이오 어촌마을": ("open_24h", "P-05", 0, 1440, False, False),
    "레이디스 마켓": ("open_range", "P-04", 660, 1410, True, False),
    "금붕어 시장": ("open_range", "P-04", 630, 1320, True, False),
    "꽃시장": ("open_range", "P-04", 420, 1170, True, False),
    # ── Day 4 ────────────────────────────────────────────────────────────
    "딤섬 브런치 · 팀호완": ("open_range", "P-04", 600, 1290, True, False),
    "타임스퀘어": ("open_range", "P-04", 600, 1320, True, False),
    "SOGO 백화점": ("open_range", "P-04", 600, 1320, True, False),
    "홍콩역 → 공항": ("open_range", "P-04", 350, 48, True, True),  # AEL 05:50-00:48
}

# 설계서 §6.9 가 "의도적으로 unknown" 이라고 못박은 문자열.
INTENTIONALLY_UNKNOWN = {
    "단지 상시 · 상점 대략 11:00–20:00",  # 주체가 둘 (단지 / 상점)
    "하행 06:00–10:00 / 상행 10:00–24:00",  # 방향별로 다르다
    "야외 상시 / 내부 대략 10:00–17:30",  # 야외 / 내부
    "매일 09:00–18:00 / 채식당 11:30–16:30",  # 사원 / 채식당
    "저녁~심야",  # 숫자 시각이 없다
    "매일 20:00 시작 · 약 10~13분",  # 개방 구간이 아니라 이벤트 시작시각
}


def _seed_spots(seed_document: dict[str, Any]) -> list[dict[str, Any]]:
    return [spot for day in seed_document["days"] for spot in day["spots"]]


class TestSeedCorpus:
    """시드 27건 전수 — 어느 문자열이 어느 판정으로 떨어지는가."""

    def test_the_expectation_table_covers_every_seed_spot(self, seed_document: dict[str, Any]) -> None:
        names = {spot["name"] for spot in _seed_spots(seed_document)}
        assert names == set(SEED_EXPECTATION), "시드 이름이 바뀌었다 — 표를 함께 고쳐라"

    def test_ac022_every_seed_hours_string_parses_as_designed(self, seed_document: dict[str, Any]) -> None:
        for spot in _seed_spots(seed_document):
            expected = SEED_EXPECTATION[spot["name"]]
            spec = parse_hours(spot["hours_text"])
            actual = (
                spec.status,
                spec.pattern,
                spec.open_min,
                spec.close_min,
                spec.approximate,
                spec.crosses_midnight,
            )
            assert actual == expected, f"{spot['name']}: {spot['hours_text']!r}"

    def test_status_distribution_matches_the_design(self, seed_document: dict[str, Any]) -> None:
        """설계서 §6.9 기대 분포: open_range 16 + 요일별 1 · open_24h 4 · unknown 6."""
        specs = [parse_hours(spot["hours_text"]) for spot in _seed_spots(seed_document)]
        assert Counter(spec.status for spec in specs) == {"open_range": 17, "unknown": 6, "open_24h": 4}
        assert Counter(spec.pattern for spec in specs) == {
            "P-04": 16,
            "P-05": 4,
            "P-03": 4,
            "P-01": 1,
            "P-06": 1,
            "P-07": 1,
        }

    def test_ac024_unknown_count_is_exactly_six_and_no_more(self, seed_document: dict[str, Any]) -> None:
        """unknown 이 **늘어도** 줄어도 실패다. 늘면 기능이 죽고, 줄면 거짓 경고가 생긴다."""
        unknown = [
            spot["name"] for spot in _seed_spots(seed_document) if parse_hours(spot["hours_text"]).status == "unknown"
        ]
        assert sorted(unknown) == sorted(
            ["심포니 오브 라이트", "PMQ", "미드레벨 에스컬레이터", "란콰이퐁 / 소호", "천단대불", "포린사원"]
        )

    @pytest.mark.parametrize("text", sorted(INTENTIONALLY_UNKNOWN))
    def test_ac024_designed_unknown_strings_stay_unknown(self, text: str) -> None:
        assert parse_hours(text).status == "unknown"

    def test_ac024_closed_text_of_every_seed_spot(self, seed_document: dict[str, Any]) -> None:
        """휴무는 타이쿤(월요일) 하나뿐. 날짜 범위 문자열은 휴무로 읽지 않는다."""
        closed = {
            spot["name"]: parse_closed(spot["closed_text"]).weekdays
            for spot in _seed_spots(seed_document)
            if parse_closed(spot["closed_text"]).weekdays
        }
        assert closed == {"타이쿤": frozenset({0})}


class TestAc022Strings:
    """AC-022 의 세 문자열을 원문 그대로."""

    def test_ac022_daily_range(self) -> None:
        spec = parse_hours("매일 08:00–18:00")
        assert (spec.status, spec.open_min, spec.close_min) == ("open_range", 480, 1080)
        assert spec.approximate is False

    def test_ac022_outdoor_24h(self) -> None:
        spec = parse_hours("야외 상시 (24h)")
        assert spec.status == "open_24h"

    def test_ac022_approximate_flag(self) -> None:
        spec = parse_hours("대략 10:00–18:00")
        assert (spec.status, spec.open_min, spec.close_min) == ("open_range", 600, 1080)
        assert spec.approximate is True

    def test_ac022_approximate_does_not_change_the_times(self) -> None:
        plain = parse_hours("매일 10:00–18:00")
        approximate = parse_hours("매일 대략 10:00–18:00")
        assert (plain.open_min, plain.close_min) == (approximate.open_min, approximate.close_min)
        assert plain.approximate is False and approximate.approximate is True


class TestRulesInIsolation:
    def test_p01_weekday_and_weekend_table(self) -> None:
        spec = parse_hours("평일 10:00–18:00 / 주말·공휴일 09:00–18:30")
        assert spec.pattern == "P-01"
        assert spec.weekday_open == {
            0: (600, 1080),
            1: (600, 1080),
            2: (600, 1080),
            3: (600, 1080),
            4: (600, 1080),
            5: (540, 1110),
            6: (540, 1110),
        }
        assert spec.open_min is None and spec.close_min is None  # 요일마다 다르다

    def test_p02_parenthetical_note_is_dropped(self) -> None:
        spec = parse_hours("매일 10:00–23:00 (갤러리 화~일 11:00–19:00)")
        assert (spec.status, spec.open_min, spec.close_min, spec.pattern) == ("open_range", 600, 1380, "P-04")

    def test_p03_two_subjects_is_unknown_even_though_both_are_readable(self) -> None:
        """읽을 수 **있는데도** 안 읽는다 — 어느 쪽이 방문 대상인지 데이터에 없다."""
        assert parse_hours("단지 상시 · 상점 대략 11:00–20:00").pattern == "P-03"

    def test_p04_crossing_midnight(self) -> None:
        spec = parse_hours("AEL 대략 05:50–00:48")
        assert (spec.open_min, spec.close_min, spec.crosses_midnight) == (350, 48, True)

    def test_p04_2400_is_1440_not_zero(self) -> None:
        spec = parse_hours("매일 대략 18:00–24:00")
        assert (spec.open_min, spec.close_min, spec.crosses_midnight) == (1080, 1440, False)

    def test_p04_ignores_non_clock_number_ranges(self) -> None:
        """`6~10분 간격` 은 시간 범위가 아니다 — 정규식이 `\\d{1,2}:\\d{2}` 를 요구한다."""
        spec = parse_hours("매일 06:30–23:30경 · 6~10분 간격")
        assert (spec.open_min, spec.close_min, spec.approximate) == (390, 1410, True)

    def test_p06_single_clock_time_is_an_event_not_opening_hours(self) -> None:
        assert parse_hours("매일 20:00 시작 · 약 10~13분").pattern == "P-06"

    def test_p07_no_clock_time_at_all(self) -> None:
        assert parse_hours("저녁~심야").pattern == "P-07"

    def test_empty_string_is_unknown(self) -> None:
        assert parse_hours("").status == "unknown"
        assert parse_hours("   ").status == "unknown"

    def test_dash_variants_are_normalised(self) -> None:
        for dash in ("-", "–", "—", "~", "〜"):
            spec = parse_hours(f"매일 10:00{dash}18:00")
            assert (spec.open_min, spec.close_min) == (600, 1080), dash

    def test_full_width_digits_are_normalised(self) -> None:
        """NFKC 전처리 — 사용자가 편집한 문자열이 들어온다(REQ-004)."""
        assert parse_hours("매일 １０:００–１８:００").open_min == 600


class TestParseClosed:
    def test_weekday_token(self) -> None:
        assert parse_closed("일부 갤러리 월요일").weekdays == frozenset({0})

    def test_multiple_weekdays(self) -> None:
        assert parse_closed("화요일·수요일 휴무").weekdays == frozenset({1, 2})

    def test_ac024_date_range_without_weekday_token_is_no_closure(self) -> None:
        assert parse_closed("(정비휴무 9/8~18은 여행 전 종료)").weekdays == frozenset()

    def test_empty_is_no_closure(self) -> None:
        assert parse_closed("").weekdays == frozenset()


class TestIsOpenAt:
    def test_unknown_is_unknown_not_closed(self) -> None:
        """`unknown` 을 `closed` 로 접으면 그 순간 거짓 경고 6건이 생긴다 (AC-024)."""
        assert is_open_at(parse_hours("저녁~심야"), 600, 0) == "unknown"

    def test_open_24h_is_always_open(self) -> None:
        spec = parse_hours("야외 상시")
        assert all(is_open_at(spec, minute, 0) == "open" for minute in (0, 720, 1439))

    @pytest.mark.parametrize(
        ("minute", "expected"),
        [
            (479, "closed"),  # 07:59 — 열기 1분 전
            (480, "open"),  # 08:00 — 개장 정각
            (720, "open"),  # 12:00
            (1080, "open"),  # 18:00 — 마감 정각은 아직 경고가 아니다 (AC-023 "이후")
            (1081, "closed"),  # 18:01
        ],
    )
    def test_open_range_boundaries(self, minute: int, expected: str) -> None:
        assert is_open_at(parse_hours("매일 08:00–18:00"), minute, 0) == expected

    def test_crossing_midnight_window(self) -> None:
        spec = parse_hours("AEL 대략 05:50–00:48")  # 05:50 → 다음날 00:48
        assert is_open_at(spec, 6 * 60, 0) == "open"
        assert is_open_at(spec, 23 * 60 + 59, 0) == "open"
        assert is_open_at(spec, 30, 0) == "open"  # 00:30
        assert is_open_at(spec, 5 * 60, 0) == "closed"  # 05:00

    def test_weekday_table_is_consulted_per_day(self) -> None:
        spec = parse_hours("평일 10:00–18:00 / 주말·공휴일 09:00–18:30")
        assert is_open_at(spec, 9 * 60 + 30, 0) == "closed"  # 월 09:30 — 아직 안 연다
        assert is_open_at(spec, 9 * 60 + 30, 5) == "open"  # 토 09:30 — 연다
        assert is_open_at(spec, 18 * 60 + 20, 6) == "open"  # 일 18:20

    def test_missing_weekday_entry_is_unknown_not_closed(self) -> None:
        spec = HoursSpec(status="open_range", weekday_open={0: (600, 1080)}, pattern="P-01")
        assert is_open_at(spec, 700, 0) == "open"
        assert is_open_at(spec, 700, 3) == "unknown"

    def test_minute_beyond_a_day_is_wrapped(self) -> None:
        """`eta_min` 은 1440 을 넘을 수 있다 — 감아서 판정한다(§6.10)."""
        spec = parse_hours("매일 08:00–18:00")
        assert is_open_at(spec, 1440 + 720, 0) == "open"
