"""외부 공급자 포트 — DSN-20 (설계서 §6.13 · §2.2 · NFR-003 · NFR-004).

포트는 `Protocol` 이다. 어댑터는 이 모양만 만족하면 되므로 테스트는 **호출 횟수를 세는
가짜**(`tests/fakes.py`)를 그대로 꽂는다 — AC-026 의 "호출 0회 / 1회" 는 반환값으로는
증명할 수 없고 카운터로만 증명된다.

스냅샷은 **정규화된 우리 형태**다. 공급자 원문을 그대로 캐시에 넣으면, 공급자가 필드를
바꾼 날 캐시에 남은 과거 데이터까지 못 읽게 된다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ExternalUnavailable",
    "FxPort",
    "FxSnapshot",
    "WeatherPort",
    "WeatherSnapshot",
    "snapshot_to_payload",
]


class ExternalUnavailable(RuntimeError):
    """공급자에서 쓸 만한 값을 못 얻었다 — 타임아웃 · 5xx · 파싱 실패 · 필드 누락.

    **이 예외는 HTTP 5xx 가 되지 않는다.** `CachedProvider` 가 받아서 stale 폴백이나
    `available:false` 로 바꾼다(NFR-004 · AC-038). 위젯 하나가 앱 전체를 죽이면 안 된다.
    """


@dataclass(frozen=True)
class WeatherSnapshot:
    """Open-Meteo 응답의 정규화 형태 (§6.13). 필드명이 그대로 API 응답에 나간다."""

    temp_c: float | None
    humidity_pct: int | None
    precipitation_mm: float | None
    weather_code: int | None
    today_max_c: float | None
    today_min_c: float | None
    precip_prob_pct: int | None
    observed_local: str | None


@dataclass(frozen=True)
class FxSnapshot:
    """Frankfurter 응답의 정규화 형태.

    `rate_micro` 는 환율 × 1_000_000 **정수**다(NFR-014 — 금액 계산에 float 을 들이지
    않는다). `rate_date` 는 공급자가 준 환율 기준일이며 **조회일과 다를 수 있다**
    (ECB 는 영업일에만 갱신한다). 이것은 고장이 아니므로 `stale` 로 표시하지 않는다.
    """

    base: str
    quote: str
    rate_micro: int
    rate_date: str | None


@runtime_checkable
class WeatherPort(Protocol):
    def fetch(self) -> WeatherSnapshot:  # 실패하면 ExternalUnavailable
        ...


@runtime_checkable
class FxPort(Protocol):
    def fetch(self) -> FxSnapshot:  # 실패하면 ExternalUnavailable
        ...


def snapshot_to_payload(snapshot: Any) -> dict[str, Any]:
    """스냅샷 → 캐시에 넣을 dict. dataclass 가 아니면 그대로 dict 로 본다."""
    if hasattr(type(snapshot), "__dataclass_fields__"):
        return asdict(snapshot)
    if isinstance(snapshot, dict):
        return dict(snapshot)
    raise TypeError(f"스냅샷을 직렬화할 수 없다: {type(snapshot)!r}")
