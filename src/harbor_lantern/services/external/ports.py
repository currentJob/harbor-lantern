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
    "NearbyPort",
    "PlaceSnapshot",
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


@dataclass(frozen=True)
class PlaceSnapshot:
    """Overpass 응답의 정규화 형태 — 근처 장소 1건 (DSN-26 · REQ-017).

    **거리와 길찾기 URL 은 여기 없다.** 둘 다 "지금 어디에 서 있는가"에 달린 값인데,
    캐시는 좌표를 양자화한 키로 공유되기 때문이다(§DSN-26). 거리는 응답을 만들 때
    사용자가 실제로 보낸 좌표로 계산한다 — 캐시에 넣으면 110m 어긋난 거리가 굳는다.
    """

    osm_type: str  # node · way · relation
    osm_id: int
    name: str
    lat: float
    lng: float
    category: str


@runtime_checkable
class WeatherPort(Protocol):
    def fetch(self) -> WeatherSnapshot:  # 실패하면 ExternalUnavailable
        ...


@runtime_checkable
class FxPort(Protocol):
    def fetch(self) -> FxSnapshot:  # 실패하면 ExternalUnavailable
        ...


@runtime_checkable
class NearbyPort(Protocol):
    """위치가 인자로 들어가는 유일한 포트 (DSN-26).

    날씨·환율은 인자가 없다(키 하나짜리 싱글턴). 근처 장소는 질의마다 키가 달라지므로
    캐시도 따로다 — `services/external/nearby.py` 를 볼 것.
    """

    def fetch(
        self,
        *,
        categories: tuple[str, ...],
        lat: float,
        lng: float,
        radius_m: int,
    ) -> tuple[PlaceSnapshot, ...]:  # 실패하면 ExternalUnavailable
        ...


def snapshot_to_payload(snapshot: Any) -> dict[str, Any]:
    """스냅샷 → 캐시에 넣을 dict. dataclass 가 아니면 그대로 dict 로 본다."""
    if hasattr(type(snapshot), "__dataclass_fields__"):
        return asdict(snapshot)
    if isinstance(snapshot, dict):
        return dict(snapshot)
    raise TypeError(f"스냅샷을 직렬화할 수 없다: {type(snapshot)!r}")
