"""설정 단일 소스 — DSN-04 (설계서 §6.1 · NFR-011 · NFR-013).

계수·TTL·임계값이 코드 곳곳에 흩어지면 "추정 계수를 설정으로 노출한다"는 약속(A8)이
거짓말이 된다. 전부 여기 한 곳에 모으고 환경변수 `HL_*` 로 덮어쓴다.

**비밀값은 없다.** 날씨·환율 두 공급자 모두 API 키가 필요 없다(§6.13). 그래도 값은
환경변수에서만 읽는다 — 하드코딩된 키 문자열이 소스에 나타나면 gitleaks 와
`tests/static/test_secrets_and_pinning.py` 가 잡는다(AC-046).

장래에 키가 필요한 공급자로 바꾸면, 키가 비었을 때 **예외를 던지지 말고**
폴백 경로(캐시 → `available:false`)를 타야 한다(NFR-004).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = [
    "DEFAULT_TIME_BAND",
    "ENV_PREFIX",
    "FIXED_TIME_BAND",
    "TIME_BANDS",
    "ExternalConfig",
    "Settings",
    "TimeBand",
    "TravelConfig",
    "load_settings",
]

ENV_PREFIX = "HL_"


# ── 이동시간 계수 (O2 · §6.7) ─────────────────────────────────────────────
@dataclass(frozen=True)
class TravelConfig:
    """**전부 추정치다.** 실제 경로탐색이 아니다(A8 · Won't).

    그래서 계산 결과에는 항상 `estimated: true` 가 붙어 나간다(AC-020).
    """

    detour_factor_walk: float = 1.35  # 직선거리 → 가로망 실제 보행거리 보정
    detour_factor_transit: float = 1.45  # 역 접근 + 노선 우회
    mode_threshold_m: float = 1200.0  # 이 이하는 걷는 편이 빠르다는 판단
    walk_speed_kmh: float = 4.8  # 성인 평균 보행
    transit_speed_kmh: float = 22.0  # MTR·버스 혼합, 정차 포함
    transit_overhead_min: int = 8  # 개찰·플랫폼 대기·환승
    min_leg_minutes: int = 1  # 거리 > 0 인데 0분이 뜨는 것을 막는다


# ── 외부 공급자 (O1 · §6.13) ──────────────────────────────────────────────
@dataclass(frozen=True)
class ExternalConfig:
    """두 공급자 모두 API 키가 없다. TTL 근거는 설계서 §6.13 표.

    - 날씨 900초: 응답 자신이 `current.interval: 900` 을 준다.
    - 환율 21600초: ECB 가 영업일 1회 갱신한다(주말 조회에 금요일 값이 온다).
    """

    weather_url: str = "https://api.open-meteo.com/v1/forecast"
    fx_url: str = "https://api.frankfurter.dev/v1/latest"
    weather_ttl_s: int = 900
    fx_ttl_s: int = 21600
    timeout_s: float = 2.5
    weather_lat: float = 22.3193  # 홍콩 중심
    weather_lng: float = 114.1694


# ── 시간대 라벨 → 기본 시작시각·체류시간 (O6 · §6.8) ──────────────────────
@dataclass(frozen=True)
class TimeBand:
    """라벨 하나의 기본값. `start_min`·`dwell_minutes` 모두 분 단위."""

    start_min: int
    dwell_minutes: int


# 라벨 목록은 시드 27건에 **실제로 등장하는 것 전부**다(설계서 §6.8).
TIME_BANDS: Mapping[str, TimeBand] = {
    "오전": TimeBand(9 * 60, 60),
    "점심": TimeBand(12 * 60, 60),
    "점심후": TimeBand(13 * 60 + 30, 75),
    "오후": TimeBand(14 * 60, 60),
    "일몰": TimeBand(17 * 60 + 30, 45),
    "이른저녁": TimeBand(18 * 60, 45),
    "저녁": TimeBand(18 * 60 + 30, 90),
    "야경": TimeBand(19 * 60 + 30, 60),
    "밤": TimeBand(21 * 60, 60),
}

# `HH:MM` 고정시각 라벨: 시작은 그 시각이고 체류 기본값만 여기서 온다.
FIXED_TIME_BAND = TimeBand(0, 15)

# 표에 없는 라벨·미상. 09:00 / 60분 — 추측하지 않고 가장 무난한 값으로 떨어진다.
DEFAULT_TIME_BAND = TimeBand(9 * 60, 60)


# ── 애플리케이션 설정 ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Settings:
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8080
    travel: TravelConfig = TravelConfig()
    external: ExternalConfig = ExternalConfig()
    join_rate_limit_n: int = 10
    join_rate_limit_window_s: int = 600
    poll_interval_s: int = 10
    default_start_date: str = "2026-10-05"  # A2
    max_display_name_len: int = 24


DEFAULT_DB_PATH = Path("harbor-lantern.db")


def _env_str(env: Mapping[str, str], key: str, default: str) -> str:
    return env.get(ENV_PREFIX + key, default)


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(ENV_PREFIX + key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_PREFIX}{key} 는 정수여야 한다 (받은 값: {raw!r})") from exc


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(ENV_PREFIX + key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_PREFIX}{key} 는 실수여야 한다 (받은 값: {raw!r})") from exc


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """환경변수에서 설정을 읽는다. `env` 를 주면 그것만 본다(테스트 결정론 · NFR-013).

    키는 전부 `HL_` 접두어 + 필드명 대문자다. 중첩 설정은 그룹 이름을 앞에 붙인다:
    `HL_TRAVEL_WALK_SPEED_KMH`, `HL_EXTERNAL_WEATHER_TTL_S`.
    값이 비어 있거나 없으면 위 dataclass 의 기본값을 그대로 쓴다.
    """
    src = os.environ if env is None else env

    base_travel = TravelConfig()
    travel = replace(
        base_travel,
        detour_factor_walk=_env_float(src, "TRAVEL_DETOUR_FACTOR_WALK", base_travel.detour_factor_walk),
        detour_factor_transit=_env_float(src, "TRAVEL_DETOUR_FACTOR_TRANSIT", base_travel.detour_factor_transit),
        mode_threshold_m=_env_float(src, "TRAVEL_MODE_THRESHOLD_M", base_travel.mode_threshold_m),
        walk_speed_kmh=_env_float(src, "TRAVEL_WALK_SPEED_KMH", base_travel.walk_speed_kmh),
        transit_speed_kmh=_env_float(src, "TRAVEL_TRANSIT_SPEED_KMH", base_travel.transit_speed_kmh),
        transit_overhead_min=_env_int(src, "TRAVEL_TRANSIT_OVERHEAD_MIN", base_travel.transit_overhead_min),
        min_leg_minutes=_env_int(src, "TRAVEL_MIN_LEG_MINUTES", base_travel.min_leg_minutes),
    )

    base_external = ExternalConfig()
    external = replace(
        base_external,
        weather_url=_env_str(src, "EXTERNAL_WEATHER_URL", base_external.weather_url),
        fx_url=_env_str(src, "EXTERNAL_FX_URL", base_external.fx_url),
        weather_ttl_s=_env_int(src, "EXTERNAL_WEATHER_TTL_S", base_external.weather_ttl_s),
        fx_ttl_s=_env_int(src, "EXTERNAL_FX_TTL_S", base_external.fx_ttl_s),
        timeout_s=_env_float(src, "EXTERNAL_TIMEOUT_S", base_external.timeout_s),
        weather_lat=_env_float(src, "EXTERNAL_WEATHER_LAT", base_external.weather_lat),
        weather_lng=_env_float(src, "EXTERNAL_WEATHER_LNG", base_external.weather_lng),
    )

    defaults = Settings(db_path=DEFAULT_DB_PATH)
    return Settings(
        db_path=Path(_env_str(src, "DB_PATH", str(DEFAULT_DB_PATH))),
        host=_env_str(src, "HOST", defaults.host),
        port=_env_int(src, "PORT", defaults.port),
        travel=travel,
        external=external,
        join_rate_limit_n=_env_int(src, "JOIN_RATE_LIMIT_N", defaults.join_rate_limit_n),
        join_rate_limit_window_s=_env_int(src, "JOIN_RATE_LIMIT_WINDOW_S", defaults.join_rate_limit_window_s),
        poll_interval_s=_env_int(src, "POLL_INTERVAL_S", defaults.poll_interval_s),
        default_start_date=_env_str(src, "DEFAULT_START_DATE", defaults.default_start_date),
        max_display_name_len=_env_int(src, "MAX_DISPLAY_NAME_LEN", defaults.max_display_name_len),
    )
