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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = [
    "DEFAULT_TIME_BAND",
    "ENV_PREFIX",
    "FIXED_TIME_BAND",
    "NEARBY_CATEGORIES",
    "NEARBY_DEFAULT_RADIUS_M",
    "NEARBY_MAX_RADIUS_M",
    "NEARBY_MIN_RADIUS_M",
    "TIME_BANDS",
    "ExternalConfig",
    "NearbyCategory",
    "NearbyConfig",
    "Settings",
    "TimeBand",
    "TravelConfig",
    "enabled_nearby_categories",
    "load_settings",
    "nearby_tag_index",
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


# ── 근처 장소 (REQ-017 · REQ-018 · DSN-26) ────────────────────────────────
# 반경 상한·기본값은 **계약**(`contracts/openapi.yaml` 의 `/api/nearby`)에 적힌 수치이며
# FastAPI `Query` 제약이 import 시점에 필요하므로 모듈 상수로 둔다. `NearbyConfig` 의
# 기본값도 이것을 쓴다 — 두 자리에 다른 숫자가 적히는 일을 없앤다.
NEARBY_MIN_RADIUS_M = 100
NEARBY_DEFAULT_RADIUS_M = 800
NEARBY_MAX_RADIUS_M = 3000


@dataclass(frozen=True)
class NearbyCategory:
    """카테고리 하나 → OSM 태그 (REQ-017).

    **이 표가 카테고리 목록의 SSoT 다.** 늘리려면 여기 한 줄을 더하고 `enabled=True` 로
    두면 된다 — 어댑터의 질의도, API 가 받는 값도, 화면의 라벨도 전부 이 표에서 나온다.
    """

    label: str
    osm_key: str
    osm_values: tuple[str, ...]
    enabled: bool = True


# 지금 노출하는 것은 **음식점·카페뿐**이다(요청 범위). `attraction` 은 확장 지점이
# 실제로 동작한다는 것을 보이려고 남겨 둔 자리이며 `enabled=False` 다 — 꺼진 카테고리를
# 요청하면 422 이고, `tests/api/test_nearby.py` 가 그 사실을 고정한다.
NEARBY_CATEGORIES: Mapping[str, NearbyCategory] = {
    "restaurant": NearbyCategory("음식점", "amenity", ("restaurant",)),
    "cafe": NearbyCategory("카페", "amenity", ("cafe",)),
    "fast_food": NearbyCategory("간편식", "amenity", ("fast_food",)),
    "attraction": NearbyCategory(
        "관광명소", "tourism", ("attraction", "museum", "viewpoint"), enabled=False,
    ),
}


def enabled_nearby_categories() -> tuple[str, ...]:
    """지금 노출되는 카테고리 이름들. 선언 순서를 유지한다(화면 정렬이 흔들리지 않게)."""
    return tuple(name for name, category in NEARBY_CATEGORIES.items() if category.enabled)


def nearby_tag_index(names: Sequence[str]) -> dict[tuple[str, str], str]:
    """`{(osm_key, osm_value): 카테고리}` — 공급자 응답을 우리 카테고리로 되돌리는 표.

    도메인(`domain/places.py`)은 설정 객체가 아니라 이 평범한 매핑만 받는다. 그래야
    정규화 함수가 설정을 모른 채 순수하게 남는다(§2.2).
    """
    index: dict[tuple[str, str], str] = {}
    for name in names:
        category = NEARBY_CATEGORIES[name]
        for value in category.osm_values:
            index[(category.osm_key, value)] = name
    return index


@dataclass(frozen=True)
class NearbyConfig:
    """Overpass(OpenStreetMap) 조회 설정. **API 키가 없다**(§6.13 과 같은 이유로).

    - `ttl_s` 1800초: 식당·카페는 분 단위로 바뀌지 않는다. 공용 무료 서비스를 상대로
      걸어 다니는 사용자가 매 요청마다 질의를 보내면 429 를 받는다(실측으로 확인했다).
    - `quantize_digits` 3자리(≈110m): 캐시 키를 만들 때 좌표를 여기까지만 쓴다.
      걸으면서 몇십 미터 움직여도 같은 캐시를 맞힌다(AC-054).
    - `max_results` 50: 남의 서버에 과한 질의를 보내지 않기 위한 어댑터 쪽 상한이다.
    """

    url: str = "https://overpass-api.de/api/interpreter"
    ttl_s: int = 1800
    # 30초. **추측이 아니라 실측이다** — 2026-09-14 침사추이 반경 500m 질의를 공용
    # 인스턴스에 보내니 성공 응답이 **10.4초** 걸렸다. 처음 잡았던 8초는 그 정상 응답조차
    # 받지 못하고 끊었다(사용자에게는 "근처 정보를 가져오지 못했습니다"로만 보였다).
    # 공용 무료 서비스라 지연이 들쭉날쭉하고 과하게 부르면 JSON 이 아닌 오류 페이지를 준다 —
    # 그래서 TTL 30분 캐시가 있고, 실패는 stale 폴백으로 흡수된다.
    timeout_s: float = 30.0
    default_radius_m: int = NEARBY_DEFAULT_RADIUS_M
    max_radius_m: int = NEARBY_MAX_RADIUS_M
    max_results: int = 50
    quantize_digits: int = 3
    user_agent: str = "harbor-lantern/0.1 (+https://github.com/currentJob/harbor-lantern)"


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
    nearby: NearbyConfig = NearbyConfig()
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

    base_nearby = NearbyConfig()
    nearby = replace(
        base_nearby,
        url=_env_str(src, "NEARBY_URL", base_nearby.url),
        ttl_s=_env_int(src, "NEARBY_TTL_S", base_nearby.ttl_s),
        timeout_s=_env_float(src, "NEARBY_TIMEOUT_S", base_nearby.timeout_s),
        default_radius_m=_env_int(src, "NEARBY_DEFAULT_RADIUS_M", base_nearby.default_radius_m),
        max_radius_m=_env_int(src, "NEARBY_MAX_RADIUS_M", base_nearby.max_radius_m),
        max_results=_env_int(src, "NEARBY_MAX_RESULTS", base_nearby.max_results),
        quantize_digits=_env_int(src, "NEARBY_QUANTIZE_DIGITS", base_nearby.quantize_digits),
        user_agent=_env_str(src, "NEARBY_USER_AGENT", base_nearby.user_agent),
    )

    defaults = Settings(db_path=DEFAULT_DB_PATH)
    return Settings(
        db_path=Path(_env_str(src, "DB_PATH", str(DEFAULT_DB_PATH))),
        host=_env_str(src, "HOST", defaults.host),
        port=_env_int(src, "PORT", defaults.port),
        travel=travel,
        external=external,
        nearby=nearby,
        join_rate_limit_n=_env_int(src, "JOIN_RATE_LIMIT_N", defaults.join_rate_limit_n),
        join_rate_limit_window_s=_env_int(src, "JOIN_RATE_LIMIT_WINDOW_S", defaults.join_rate_limit_window_s),
        poll_interval_s=_env_int(src, "POLL_INTERVAL_S", defaults.poll_interval_s),
        default_start_date=_env_str(src, "DEFAULT_START_DATE", defaults.default_start_date),
        max_display_name_len=_env_int(src, "MAX_DISPLAY_NAME_LEN", defaults.max_display_name_len),
    )
