"""날씨 어댑터 — Open-Meteo (설계서 §6.13 · REQ-014).

API 키가 필요 없다(NFR-011 — 소스에 키 문자열이 없다). TTL 900초의 근거는 응답 자신이
`current.interval: 900` 을 준다는 것이다 — 15분보다 자주 물어도 새 값이 없다.

**테스트에서 실제로 호출되면 안 된다.** `tests/conftest.py` 의 네트워크 차단이 세션
전역으로 걸려 있어 아웃바운드는 그 자리에서 실패한다. 어댑터 테스트는 `client` 인자로
`httpx.MockTransport` 를 꽂아 응답 샘플을 주입한다(NFR-003 · AC-037).
"""

from __future__ import annotations

from typing import Any

import httpx

from harbor_lantern.config import ExternalConfig
from harbor_lantern.services.external.ports import ExternalUnavailable, WeatherSnapshot

__all__ = ["OpenMeteoWeatherAdapter"]

_CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,precipitation,weather_code"
_DAILY_FIELDS = "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"


class OpenMeteoWeatherAdapter:
    """`WeatherPort` 구현. 아웃바운드 HTTP 는 `services/external/` 안에서만 일어난다(§2.2)."""

    def __init__(self, cfg: ExternalConfig, client: httpx.Client | None = None) -> None:
        self._cfg = cfg
        self._client = client

    def fetch(self) -> WeatherSnapshot:
        params = {
            "latitude": self._cfg.weather_lat,
            "longitude": self._cfg.weather_lng,
            "current": _CURRENT_FIELDS,
            "daily": _DAILY_FIELDS,
            "timezone": "Asia/Hong_Kong",
            "forecast_days": 1,
        }
        try:
            if self._client is not None:
                response = self._client.get(self._cfg.weather_url, params=params)
            else:
                with httpx.Client(timeout=self._cfg.timeout_s) as client:
                    response = client.get(self._cfg.weather_url, params=params)
            response.raise_for_status()
            document = response.json()
        except Exception as exc:  # 타임아웃 · 5xx · JSON 파싱 실패 전부 한 종류로 좁힌다
            raise ExternalUnavailable(f"open-meteo 호출 실패: {exc}") from exc
        return _to_snapshot(document)


def _to_snapshot(document: Any) -> WeatherSnapshot:
    if not isinstance(document, dict):
        raise ExternalUnavailable("open-meteo 응답이 객체가 아니다")
    current = document.get("current")
    daily = document.get("daily")
    if not isinstance(current, dict):
        raise ExternalUnavailable("open-meteo 응답에 current 가 없다")
    daily = daily if isinstance(daily, dict) else {}
    snapshot = WeatherSnapshot(
        temp_c=_number(current.get("temperature_2m")),
        humidity_pct=_integer(current.get("relative_humidity_2m")),
        precipitation_mm=_number(current.get("precipitation")),
        weather_code=_integer(current.get("weather_code")),
        today_max_c=_number(_first(daily.get("temperature_2m_max"))),
        today_min_c=_number(_first(daily.get("temperature_2m_min"))),
        precip_prob_pct=_integer(_first(daily.get("precipitation_probability_max"))),
        observed_local=_text(current.get("time")),
    )
    if snapshot.temp_c is None:
        # 온도조차 없으면 위젯에 띄울 것이 없다 — 캐시를 이런 값으로 덮으면
        # 다음 성공까지 화면이 빈다. 실패로 보고 마지막 성공값을 유지한다.
        raise ExternalUnavailable("open-meteo 응답에 기온이 없다")
    return snapshot


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
