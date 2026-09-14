"""외부 공급자 포트의 가짜 구현 — 호출 카운터 포함 (설계서 §2.2 · §6.13 · AC-026).

**카운터가 이 파일의 존재 이유다.** AC-026 은 "TTL 안에서는 어댑터 호출 0회,
만료 후 동시 요청 n건에도 1회"를 요구한다. 그건 반환값으로는 증명할 수 없고
**몇 번 불렀는지 세는 것**으로만 증명된다.

가짜는 스냅샷 타입을 모른다. `WeatherSnapshot`·`FxSnapshot` 은 IMP-B 의
`services/external/ports.py` 소유라 여기서 import 하면 T0 이 T2 를 기다려야 한다.
그래서 **주는 것을 그대로 돌려준다** — 테스트가 진짜 스냅샷을 만들어 넣으면 된다.

    port = FakeWeatherPort(WeatherSnapshot(**WEATHER_SAMPLE))
    ...
    assert port.calls == 1

실패 주입은 속성을 갈아 끼운다(호출 사이에 바꿔도 된다 — AC-027 의 stale 폴백):

    port.error = ExternalUnavailable("timeout")
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "FX_SAMPLE",
    "PLACES_SAMPLE",
    "WEATHER_SAMPLE",
    "FakeFxPort",
    "FakeFxPortError",
    "FakeNearbyPort",
    "FakePort",
    "FakeWeatherPort",
]

# 설계서 §6.13 이 2026-09-13 에 실제로 호출해 받은 응답을, 우리 정규화 형태의
# 필드명으로 옮긴 것이다. 스냅샷 타입이 생기면 `WeatherSnapshot(**WEATHER_SAMPLE)`.
WEATHER_SAMPLE: dict[str, Any] = {
    "temp_c": 28.0,
    "humidity_pct": 79,
    "precipitation_mm": 0.0,
    "weather_code": 3,
    "today_max_c": 28.2,
    "today_min_c": 24.4,
    "precip_prob_pct": 100,
    "observed_local": "2026-09-13T15:00",
}

FX_SAMPLE: dict[str, Any] = {
    "base": "HKD",
    "quote": "KRW",
    "rate_micro": 171_230_000,  # 171.23 × 1_000_000 — 환율도 정수로 굳힌다(NFR-014)
    "rate_date": "2026-09-11",  # 조회일이 아니다. ECB 직전 영업일이다(§6.13)
}


class FakePort:
    """`fetch()` 하나짜리 포트의 공통 가짜.

    `error` 가 설정돼 있으면 그것을 던지고, 아니면 `result` 를 돌려준다.
    둘 다 호출 **후**에 세므로, 예외가 나도 카운터는 올라간다 — 실패한 호출도
    외부를 때린 것이다.
    """

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def fetch(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    def reset(self) -> None:
        self.calls = 0

    def __repr__(self) -> str:
        return f"{type(self).__name__}(calls={self.calls}, error={self.error!r})"


class FakeWeatherPort(FakePort):
    """`WeatherPort` 자리에 꽂는 가짜 (REQ-014)."""


class FakeFxPort(FakePort):
    """`FxPort` 자리에 꽂는 가짜 (REQ-015)."""


class FakeNearbyPort:
    """`NearbyPort` 자리에 꽂는 가짜 (REQ-017 · AC-054 · AC-055).

    `FakePort` 를 물려받지 않는다 — 이 포트의 `fetch()` 는 **인자를 받는다**(위치·반경·
    카테고리). 마지막 호출 인자를 `last_call` 에 남겨, 캐시 키가 실제로 인자를 구분하는지
    검증할 수 있게 한다.
    """

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result if result is not None else PLACES_SAMPLE
        self.error = error
        self.calls = 0
        self.last_call: dict[str, Any] | None = None

    def fetch(self, *, categories: tuple[str, ...], lat: float, lng: float,
              radius_m: int) -> Any:
        self.calls += 1
        self.last_call = {
            "categories": categories, "lat": lat, "lng": lng, "radius_m": radius_m,
        }
        if self.error is not None:
            raise self.error
        return self.result

    def reset(self) -> None:
        self.calls = 0
        self.last_call = None

    def __repr__(self) -> str:
        return f"FakeNearbyPort(calls={self.calls}, error={self.error!r})"


# 침사추이(스타 애비뉴 부근) 기준 표본. 좌표는 실재하는 위치대이지만 이름은
# 테스트용이다 — **실제 Overpass 응답을 그대로 박아 두지 않는다**(가게는 문을 닫는다).
PLACES_SAMPLE: tuple[dict[str, Any], ...] = (
    {"osm_type": "node", "osm_id": 1001, "name": "가까운 국수집",
     "lat": 22.2940, "lng": 114.1735, "category": "restaurant"},
    {"osm_type": "node", "osm_id": 1002, "name": "하버뷰 카페",
     "lat": 22.2950, "lng": 114.1750, "category": "cafe"},
    {"osm_type": "way", "osm_id": 2001, "name": "먼 분식",
     "lat": 22.3000, "lng": 114.1800, "category": "fast_food"},
)


class FakeFxPortError(RuntimeError):
    """공급자 실패를 흉내 낼 때 쓰는 기본 예외.

    IMP-B 가 `ExternalUnavailable` 을 정의하면 그것을 쓰는 편이 낫다 — 이건
    그 타입이 없어도 폴백 테스트를 쓸 수 있게 두는 자리다.
    """
