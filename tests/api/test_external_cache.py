"""외부 공급자 캐시 · stale 폴백 · single-flight — AC-026 · AC-027 · AC-028 · AC-038.

(REQ-014 · REQ-015 · NFR-003 · NFR-004 · 설계서 §6.13 · §12 F6)

**호출 횟수를 세는 것이 이 파일의 요점이다.** "캐시가 동작한다"는 반환값으로는 증명되지
않는다 — 어댑터를 몇 번 불렀는지로만 증명된다. 그래서 `tests/fakes.py` 의 `.calls` 를 쓴다.

여기서 실제 네트워크로 나가는 경로는 하나도 없다. `tests/conftest.py` 의 차단이
세션 전역이라, 실수로 진짜 호출이 남으면 그 자리에서 실패한다(AC-037).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import pytest

from harbor_lantern.services.external.cache import CachedProvider
from harbor_lantern.services.external.frankfurter import FrankfurterFxAdapter
from harbor_lantern.services.external.openmeteo import OpenMeteoWeatherAdapter
from harbor_lantern.services.external.ports import ExternalUnavailable, FxSnapshot, WeatherSnapshot
from tests.fakes import FX_SAMPLE, WEATHER_SAMPLE, FakeFxPort, FakeWeatherPort

WEATHER_TTL = 900
FX_TTL = 21_600


@pytest.fixture
def weather_port(app: Any) -> FakeWeatherPort:
    port = FakeWeatherPort(WeatherSnapshot(**WEATHER_SAMPLE))
    app.state.weather_provider = CachedProvider(port, "weather:hk", WEATHER_TTL, app.state.db, app.state.clock)
    return port


# ── AC-026 · AC-028: TTL 안에서는 호출 0회 ────────────────────────────────
def test_cache_hit_does_not_call_the_adapter(client: Any, weather_port: FakeWeatherPort, fixed_clock: Any) -> None:
    """AC-026: 첫 요청에서만 1회 호출하고, TTL 안의 이후 요청은 **호출 0회**다."""
    first = client.get("/api/weather")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["available"] is True
    assert body["stale"] is False
    assert body["temp_c"] == 28.0
    assert body["humidity_pct"] == 79
    assert body["source"] == "open-meteo"
    assert weather_port.calls == 1

    fixed_clock.advance(WEATHER_TTL - 1)
    again = client.get("/api/weather")
    assert again.json() == body
    assert weather_port.calls == 1, "TTL 안인데 어댑터를 다시 불렀다"


def test_cache_refreshes_once_after_expiry(client: Any, weather_port: FakeWeatherPort, fixed_clock: Any) -> None:
    """AC-026: 만료 후 **첫 요청에서만** 1회 갱신한다."""
    client.get("/api/weather")
    fixed_clock.advance(WEATHER_TTL + 1)

    weather_port.result = WeatherSnapshot(**{**WEATHER_SAMPLE, "temp_c": 31.5})
    refreshed = client.get("/api/weather")
    assert refreshed.json()["temp_c"] == 31.5
    assert refreshed.json()["stale"] is False
    assert weather_port.calls == 2

    client.get("/api/weather")
    assert weather_port.calls == 2


def test_fx_reports_rate_and_date(client: Any, fx_port: FakeFxPort, fixed_clock: Any) -> None:
    """AC-028: 환율은 `rate_micro`(정수)와 기준일을 주고, 캐시 규칙은 날씨와 같다.

    `rate_date` 는 **조회일이 아니다** — ECB 는 영업일에만 갱신하므로 주말에는 금요일
    값이 온다. 그것은 고장이 아니라서 `stale` 로 표시하지 않는다.
    """
    body = client.get("/api/fx").json()
    assert body["base"] == "HKD"
    assert body["quote"] == "KRW"
    assert body["rate_micro"] == FX_SAMPLE["rate_micro"]
    assert body["rate_date"] == "2026-09-11"
    assert body["stale"] is False
    assert body["fetched_at"] == "2026-10-05T02:00:00Z"
    assert body["source"] == "frankfurter"
    assert fx_port.calls == 1

    fixed_clock.advance(FX_TTL - 1)
    client.get("/api/fx")
    assert fx_port.calls == 1


# ── AC-027 · AC-038: 실패해도 200 ─────────────────────────────────────────
def test_stale_fallback_serves_the_last_success(client: Any, weather_port: FakeWeatherPort, fixed_clock: Any) -> None:
    """AC-027: 갱신이 실패하면 마지막 성공 캐시를 `stale=true` · `fetched_at` 과 함께 200 으로."""
    first = client.get("/api/weather").json()
    fetched_at = first["fetched_at"]

    fixed_clock.advance(WEATHER_TTL + 60)
    weather_port.error = ExternalUnavailable("공급자 장애")

    fallback = client.get("/api/weather")
    assert fallback.status_code == 200
    body = fallback.json()
    assert body["stale"] is True
    assert body["available"] is True
    assert body["temp_c"] == first["temp_c"]
    assert body["fetched_at"] == fetched_at, "stale 응답의 시각은 마지막 **성공** 시각이다"
    assert weather_port.calls == 2


def test_no_cache_plus_failure_is_available_false_and_the_rest_still_works(
    client: Any,
    weather_port: FakeWeatherPort,
    trip: Any,
) -> None:
    """AC-027 후반 · AC-038: 캐시가 없는 상태의 실패는 `available:false` 200 이고,
    일정·경비 API 는 **정상 응답**한다. 위젯 하나가 앱 전체를 죽이면 안 된다."""
    weather_port.error = ExternalUnavailable("첫 호출부터 실패")

    response = client.get("/api/weather")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "available": False,
        "stale": False,
        "fetched_at": None,
        "temp_c": None,
        "humidity_pct": None,
        "precipitation_mm": None,
        "weather_code": None,
        "today_max_c": None,
        "today_min_c": None,
        "precip_prob_pct": None,
        "observed_local": None,
        "source": "open-meteo",
    }

    assert trip.state().status_code == 200
    assert client.get(f"{trip.base}/expenses", headers=trip.headers).status_code == 200


def test_recovery_after_failure(client: Any, weather_port: FakeWeatherPort, fixed_clock: Any) -> None:
    """AC-027 파생: 공급자가 돌아오면 다음 요청에서 `stale` 이 내려간다."""
    weather_port.error = ExternalUnavailable("장애")
    assert client.get("/api/weather").json()["available"] is False

    weather_port.error = None
    body = client.get("/api/weather").json()
    assert body["available"] is True
    assert body["stale"] is False


def test_external_endpoints_need_no_token(client: Any, weather_port: FakeWeatherPort, fx_port: FakeFxPort) -> None:
    """AC-026 경계: 날씨·환율은 공개다(참가자 토큰을 요구하지 않는다)."""
    assert client.get("/api/weather").status_code == 200
    assert client.get("/api/fx").status_code == 200


# ── §12 F6: single-flight ─────────────────────────────────────────────────
class _SlowPort:
    """호출이 겹치도록 일부러 느린 포트. 락이 없으면 두 스레드가 **둘 다** 외부를 때린다."""

    def __init__(self, snapshot: Any) -> None:
        self._snapshot = snapshot
        self._guard = threading.Lock()
        self.calls = 0
        self.entered = threading.Event()

    def fetch(self) -> Any:
        with self._guard:
            self.calls += 1
        self.entered.set()
        time.sleep(0.15)
        return self._snapshot


def test_concurrent_expiry_calls_the_adapter_once(app: Any) -> None:
    """AC-026 · §12 F6: 만료 순간 동시 요청 2건에도 어댑터 호출은 **1회**다.

    락이 없으면 단일 요청 테스트는 전부 통과하고 부하에서만 깨진다 — 그래서 여기서만
    실제로 스레드를 띄운다.
    """
    port = _SlowPort(WeatherSnapshot(**WEATHER_SAMPLE))
    provider = CachedProvider(port, "weather:hk", WEATHER_TTL, app.state.db, app.state.clock)
    results: list[Any] = []

    def call() -> None:
        results.append(provider.get())

    first = threading.Thread(target=call)
    second = threading.Thread(target=call)
    first.start()
    port.entered.wait(timeout=2)  # 두 번째 스레드가 확실히 "만료된 캐시"를 보게 한다
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert port.calls == 1, f"동시 요청이 외부를 {port.calls}번 때렸다 (single-flight 없음)"
    assert len(results) == 2
    assert all(result.available and not result.stale for result in results)
    assert results[0].payload == results[1].payload


# ── AC-038: 어댑터에 타임아웃·5xx·잘못된 페이로드 주입 ─────────────────────
def _weather_adapter(handler: Any, settings: Any) -> OpenMeteoWeatherAdapter:
    return OpenMeteoWeatherAdapter(settings.external, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _fx_adapter(handler: Any, settings: Any) -> FrankfurterFxAdapter:
    return FrankfurterFxAdapter(settings.external, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


def _server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text="upstream is down")


def _garbage(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>not json</html>")


def _missing_fields(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"current": {"time": "2026-09-13T15:00"}, "daily": {}})


@pytest.mark.parametrize("handler", [_timeout, _server_error, _garbage, _missing_fields])
def test_weather_adapter_turns_every_failure_into_one_exception(handler: Any, settings: Any) -> None:
    """AC-038: 타임아웃·5xx·잘못된 페이로드·필드 누락이 전부 `ExternalUnavailable` 이다.

    실패 종류마다 다른 예외가 새어 나가면 그중 하나는 반드시 500 이 된다.
    """
    with pytest.raises(ExternalUnavailable):
        _weather_adapter(handler, settings).fetch()


@pytest.mark.parametrize("handler", [_timeout, _server_error, _garbage])
def test_fx_adapter_turns_every_failure_into_one_exception(handler: Any, settings: Any) -> None:
    """AC-038 (환율)."""
    with pytest.raises(ExternalUnavailable):
        _fx_adapter(handler, settings).fetch()


def test_adapters_parse_the_recorded_live_responses(settings: Any) -> None:
    """AC-026 파생: 설계서 §6.13 이 2026-09-13 에 실제로 받은 응답을 우리 형태로 옮긴다.

    (응답 샘플을 주입한다 — 테스트는 절대 진짜 공급자를 부르지 않는다.)
    """
    weather_document = {
        "current": {
            "time": "2026-09-13T15:00",
            "interval": 900,
            "temperature_2m": 28.0,
            "relative_humidity_2m": 79,
            "precipitation": 0.0,
            "weather_code": 3,
        },
        "daily": {
            "time": ["2026-09-13"],
            "temperature_2m_max": [28.2],
            "temperature_2m_min": [24.4],
            "precipitation_probability_max": [100],
            "weather_code": [96],
        },
    }
    snapshot = _weather_adapter(lambda request: httpx.Response(200, json=weather_document), settings).fetch()
    assert snapshot == WeatherSnapshot(**WEATHER_SAMPLE)

    fx_document = {"amount": 1.0, "base": "HKD", "date": "2026-09-11", "rates": {"KRW": 171.23}}
    rate = _fx_adapter(lambda request: httpx.Response(200, json=fx_document), settings).fetch()
    assert rate == FxSnapshot(**FX_SAMPLE)
    assert isinstance(rate.rate_micro, int), "환율은 정수로 굳혀서 들고 다닌다 (NFR-014)"


def test_endpoint_stays_200_when_the_adapter_fails(client: Any, app: Any, settings: Any) -> None:
    """AC-038 (통합): 어댑터가 5xx 를 받아도 엔드포인트는 **200 + available:false** 다."""
    app.state.weather_provider = CachedProvider(
        _weather_adapter(_server_error, settings),
        "weather:hk",
        WEATHER_TTL,
        app.state.db,
        app.state.clock,
    )
    response = client.get("/api/weather")
    assert response.status_code == 200
    assert response.json()["available"] is False
