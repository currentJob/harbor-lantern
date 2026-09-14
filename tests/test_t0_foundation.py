"""T0 산출물 자체 검증 — 시드 전사 · 반올림 · 시계 · 설정.

여기 있는 것은 **T0 이 만든 것**만이다. 도메인 계산(T1)·API(T2)·프론트(T3)의 AC 검증은
각 담당의 `tests/domain`·`tests/api`·`tests/static` 이 맡는다.

시드 전사 검증이 이 파일의 첫 자리인 이유: 좌표 오타 하나가 지도에 바다 한가운데
핀을 찍는데, 그건 **아무 예외도 만들지 않는다.**
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest

from harbor_lantern.clock import FixedClock, SystemClock
from harbor_lantern.config import TIME_BANDS, load_settings
from harbor_lantern.domain.util import (
    add_days,
    day_offset,
    format_hhmm,
    format_iso_utc,
    is_hhmm,
    parse_hhmm,
    parse_iso_utc,
    round_half_up,
    weekday_of,
)

# 설계서 §5.3 · seed-spots.schema.json 이 못박은 값.
EXPECTED_PER_DAY = [6, 9, 8, 4]
EXPECTED_TOTAL = 27
# 홍콩 대략 경계. 전사 실수를 잡는 것이 목적이라 넉넉하게 잡는다.
HK_LAT = (22.1, 22.6)
HK_LNG = (113.8, 114.5)

SPOT_FIELDS = {
    "time_label",
    "name",
    "name_original",
    "tip",
    "hours_text",
    "closed_text",
    "description",
    "recommendation",
    "lat",
    "lng",
    "dwell_minutes",
}


def _spots(seed_document: dict[str, Any]) -> list[dict[str, Any]]:
    return [spot for day in seed_document["days"] for spot in day["spots"]]


class TestSeedTranscription:
    def test_header(self, seed_document: dict[str, Any]) -> None:
        assert seed_document["schema_version"] == "1.0"
        assert seed_document["source"] == "reference/original-static-page.html"

    def test_day_and_spot_counts(self, seed_document: dict[str, Any]) -> None:
        days = seed_document["days"]
        assert [d["day_index"] for d in days] == [1, 2, 3, 4]
        assert [len(d["spots"]) for d in days] == EXPECTED_PER_DAY
        assert len(_spots(seed_document)) == EXPECTED_TOTAL

    def test_day_start_local(self, seed_document: dict[str, Any]) -> None:
        # Day 1 은 첫 스팟이 '오후'라 14:00, 나머지는 '오전'이라 09:00 (§5.3).
        assert [d["start_local"] for d in seed_document["days"]] == ["14:00", "09:00", "09:00", "09:00"]

    def test_day_colors_are_lowercase_hex(self, seed_document: dict[str, Any]) -> None:
        colors = [d["color"] for d in seed_document["days"]]
        assert colors == ["#22d3ee", "#ff2e88", "#f7b733", "#a78bfa"]

    def test_every_spot_has_exactly_the_contract_fields(self, seed_document: dict[str, Any]) -> None:
        for spot in _spots(seed_document):
            assert set(spot) == SPOT_FIELDS, spot.get("name")

    def test_coordinates_are_inside_hong_kong(self, seed_document: dict[str, Any]) -> None:
        for spot in _spots(seed_document):
            assert HK_LAT[0] <= spot["lat"] <= HK_LAT[1], spot["name"]
            assert HK_LNG[0] <= spot["lng"] <= HK_LNG[1], spot["name"]

    def test_names_are_unique_and_nonempty(self, seed_document: dict[str, Any]) -> None:
        names = [spot["name"] for spot in _spots(seed_document)]
        assert all(names)
        assert len(set(names)) == len(names)

    def test_time_labels_are_known_bands_or_fixed_times(self, seed_document: dict[str, Any]) -> None:
        # 라벨이 표에 없으면 기본값(09:00/60분)으로 조용히 떨어진다 — 그건 전사 실수의
        # 증상이지 정상이 아니다. 그래서 여기서 잡는다.
        for spot in _spots(seed_document):
            label = spot["time_label"]
            assert label in TIME_BANDS or is_hhmm(label), label

    def test_dwell_is_unset_in_seed(self, seed_document: dict[str, Any]) -> None:
        # 체류시간은 원본에 없는 파생 필드다. 시드가 값을 넣으면 O6 의 기본값 표가 죽는다.
        assert all(spot["dwell_minutes"] is None for spot in _spots(seed_document))

    def test_hours_text_is_raw(self, seed_document: dict[str, Any]) -> None:
        # 미리 파싱해서 넣지 않는다(A1 · AC-024). 원본에만 있는 문자열 몇 개를 표본으로 고정.
        texts = {spot["name"]: spot["hours_text"] for spot in _spots(seed_document)}
        assert texts["스타 애비뉴"] == "야외 상시 (24h)"
        assert texts["만모사원"] == "매일 08:00–18:00 · 무료"
        assert texts["옹핑360 케이블카"] == "평일 10:00–18:00 / 주말·공휴일 09:00–18:30"
        assert texts["미드레벨 에스컬레이터"] == "하행 06:00–10:00 / 상행 10:00–24:00"

    def test_fixed_time_spot_is_the_light_show(self, seed_document: dict[str, Any]) -> None:
        fixed = [s["name"] for s in _spots(seed_document) if is_hhmm(s["time_label"])]
        assert fixed == ["심포니 오브 라이트"]


class TestRoundHalfUp:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0.5, 1), (1.5, 2), (2.5, 3), (0.4999, 0), (0.49999999999999994, 0), (-1.5, -1), (-2.5, -2), (7, 7)],
    )
    def test_ties_go_up(self, value: float, expected: int) -> None:
        assert round_half_up(value) == expected

    def test_differs_from_builtin_round(self) -> None:
        # 내장 round() 는 은행가 반올림이다. 이 차이가 F4 의 전부다.
        assert round(0.5) == 0 and round_half_up(0.5) == 1
        assert round(2.5) == 2 and round_half_up(2.5) == 3

    def test_progress_percent_shape(self) -> None:
        # 진행률: 27스팟 중 14개 완료 → 51.85% → 52 (§6.19)
        assert round_half_up(14 / 27 * 100) == 52


class TestTimeHelpers:
    def test_hhmm_roundtrip(self) -> None:
        assert parse_hhmm("09:00") == 540
        assert parse_hhmm("20:00") == 1200
        assert parse_hhmm("24:00") == 1440  # 영업시간 원문에 실제로 나온다(§6.9)
        assert parse_hhmm("저녁") is None
        assert format_hhmm(540) == "09:00"

    def test_past_midnight_keeps_the_day(self) -> None:
        # eta_min 은 1440 을 넘을 수 있다. 시각만 남기고 날짜를 잃으면 안 된다(§6.2).
        assert format_hhmm(1500) == "01:00"
        assert day_offset(1500) == 1
        assert day_offset(1439) == 0

    def test_is_hhmm_only_matches_clock_labels(self) -> None:
        assert is_hhmm("20:00")
        assert not is_hhmm("오후")
        assert not is_hhmm("24:00")  # 시간대 라벨로는 쓰이지 않는다

    def test_weekday_and_date_math(self) -> None:
        assert weekday_of("2026-10-05") == 0  # 월요일 — 시드 Day 1 의 '10.5 월'
        assert weekday_of("2026-10-07") == 2
        assert add_days("2026-10-05", 3) == "2026-10-08"

    def test_iso_utc_roundtrip(self) -> None:
        moment = parse_iso_utc("2026-10-05T01:23:45Z")
        assert format_iso_utc(moment) == "2026-10-05T01:23:45Z"

    def test_naive_datetime_is_refused(self) -> None:
        from datetime import datetime as _dt

        with pytest.raises(ValueError, match="naive"):
            format_iso_utc(_dt(2026, 10, 5, 1, 23, 45))


class TestClocks:
    def test_fixed_clock_does_not_move_on_its_own(self, fixed_clock: FixedClock) -> None:
        assert fixed_clock.now_utc() == fixed_clock.now_utc()

    def test_advance_accepts_seconds_or_timedelta(self, fixed_clock: FixedClock) -> None:
        start = fixed_clock.now_utc()
        fixed_clock.advance(900)
        assert fixed_clock.now_utc() - start == timedelta(seconds=900)
        fixed_clock.advance(timedelta(hours=6))
        assert fixed_clock.now_utc() - start == timedelta(seconds=900) + timedelta(hours=6)

    def test_fixed_clock_refuses_naive(self) -> None:
        from datetime import datetime as _dt

        with pytest.raises(ValueError, match="tz-aware"):
            FixedClock(_dt(2026, 10, 5))

    def test_system_clock_is_utc_aware(self) -> None:
        now = SystemClock().now_utc()
        assert now.tzinfo is not None and now.utcoffset() == timedelta(0)


class TestSettings:
    def test_defaults_match_the_design(self) -> None:
        settings = load_settings(env={})
        assert settings.host == "127.0.0.1"
        assert settings.port == 8080
        assert settings.default_start_date == "2026-10-05"
        assert settings.travel.mode_threshold_m == 1200.0
        assert settings.travel.transit_overhead_min == 8
        assert settings.external.weather_ttl_s == 900
        assert settings.external.fx_ttl_s == 21600
        assert settings.join_rate_limit_n == 10

    def test_env_overrides_are_read_from_the_given_mapping_only(self) -> None:
        settings = load_settings(env={"HL_PORT": "9999", "HL_TRAVEL_WALK_SPEED_KMH": "5.5"})
        assert settings.port == 9999
        assert settings.travel.walk_speed_kmh == 5.5
        assert settings.travel.transit_speed_kmh == 22.0  # 나머지는 기본값 그대로

    def test_bad_env_value_says_which_key(self) -> None:
        with pytest.raises(ValueError, match="HL_PORT"):
            load_settings(env={"HL_PORT": "여덟천팔십"})

    def test_no_secret_looking_defaults(self) -> None:
        # 비밀값은 없다(§6.1). 기본 URL 에 키·토큰 쿼리가 붙어 있으면 그 자체가 사고다.
        settings = load_settings(env={})
        for url in (settings.external.weather_url, settings.external.fx_url):
            assert re.search(r"(?i)(api[_-]?key|token|secret)", url) is None


class TestSchemaSql:
    def test_ddl_is_idempotent_and_applies_twice(self, sqlite_conn: Any, schema_sql: str) -> None:
        # 기동마다 그대로 적용한다(§9). 두 번 걸어도 터지지 않아야 한다.
        sqlite_conn.executescript(schema_sql)
        tables = {row[0] for row in sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "trip",
            "participant",
            "day",
            "spot",
            "visit",
            "expense",
            "expense_share",
            "external_cache",
            "join_attempt",
        } <= tables

    def test_foreign_keys_are_on_for_this_connection(self, sqlite_conn: Any) -> None:
        # 연결마다 켜야 한다 — 기본은 OFF 다(§12 F5).
        assert sqlite_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_expense_currency_is_locked_to_hkd(self, sqlite_conn: Any) -> None:
        import sqlite3 as _sqlite3

        sqlite_conn.execute(
            "INSERT INTO trip (id, name, start_date, invite_code, created_at)"
            " VALUES ('t1', 'x', '2026-10-05', 'ABCDEFGHJKMN', '2026-10-01T00:00:00Z')"
        )
        sqlite_conn.execute(
            "INSERT INTO participant (id, trip_id, display_name, token_hash, joined_at)"
            " VALUES ('p1', 't1', '나', 'h', '2026-10-01T00:00:00Z')"
        )
        with pytest.raises(_sqlite3.IntegrityError):
            sqlite_conn.execute(
                "INSERT INTO expense (id, trip_id, payer_id, amount_minor, currency, spent_at, created_at)"
                " VALUES ('e1', 't1', 'p1', 100, 'KRW', '2026-10-05T00:00:00Z', '2026-10-05T00:00:00Z')"
            )
