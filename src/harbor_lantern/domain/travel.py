"""이동시간 엔진 — DSN-14 (설계서 §6.7 · REQ-010 · O2 · AC-020).

**여기 있는 계수는 전부 추정치다.** 실제 MTR·버스 경로탐색이 아니다(A8 · Won't).
직선거리 × 우회계수 ÷ 모드별 평균속도 + 고정 환승 여유 — 그것뿐이다. 그래서 결과
`Leg` 에는 항상 `estimated=True` 가 붙어 나가고, 계수는 `TravelConfig` 로 노출된다.
"""

from __future__ import annotations

from collections.abc import Sequence

from harbor_lantern.config import TravelConfig
from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng, Leg
from harbor_lantern.domain.util import round_half_up

__all__ = ["day_totals", "leg"]

_SECONDS_PER_HOUR = 3600.0
_METERS_PER_KM = 1000.0


def leg(a: LatLng, b: LatLng, cfg: TravelConfig) -> Leg:
    """한 구간의 거리·모드·이동시간 추정 (설계서 §6.7).

    `travel_seconds`(주행)와 `overhead_seconds`(환승·대기)를 **따로** 담는다.
    대중교통은 환승 여유가 **덧셈 항**이라 우회계수를 2배로 해도 `minutes` 가 2배가
    되지 않는다 — 비례성(AC-020)은 곱해지는 주행 성분에 대해서만 성립한다.
    그래서 검증은 `travel_seconds` 로, 테스트는 도보 모드로 한다(§12 F8).

    거리가 0 이면 `minutes` 도 0 이다. 같은 자리를 옮겨 적은 스팟에 "최소 1분"을
    붙이면 하루 전체가 이유 없이 밀린다.
    """
    distance_m = haversine_m(a, b)
    is_walk = distance_m <= cfg.mode_threshold_m

    factor = cfg.detour_factor_walk if is_walk else cfg.detour_factor_transit
    speed_kmh = cfg.walk_speed_kmh if is_walk else cfg.transit_speed_kmh
    speed_mps = speed_kmh * _METERS_PER_KM / _SECONDS_PER_HOUR

    # 내장 round() 는 은행가 반올림이라 .5 에서 프론트(JS)와 갈린다(§12 F4).
    travel_seconds = round_half_up(distance_m * factor / speed_mps)
    overhead_seconds = 0 if is_walk else cfg.transit_overhead_min * 60

    if distance_m == 0.0:
        minutes = 0
    else:
        minutes = max(cfg.min_leg_minutes, round_half_up((travel_seconds + overhead_seconds) / 60.0))

    return Leg(
        distance_m=distance_m,
        mode="walk" if is_walk else "transit",
        travel_seconds=travel_seconds,
        overhead_seconds=overhead_seconds,
        minutes=minutes,
        estimated=True,
    )


def day_totals(legs: Sequence[Leg | None]) -> tuple[float, int]:
    """(일자 총 이동거리 m, 총 이동 분). `None` 구간(마지막 스팟 뒤)은 건너뛴다.

    `build_timeline` 이 스팟과 **길이가 같은** 구간 목록을 돌려주고 마지막 칸이
    `None` 이다(카드마다 "다음 스팟까지"를 붙이기 위해서다 · §6.15). 그 목록을
    그대로 넘길 수 있어야 호출부가 필터링 코드를 또 쓰지 않는다.

    AC-019: 일자 총 거리 == 구간 거리의 합.
    """
    present = [item for item in legs if item is not None]
    return sum(item.distance_m for item in present), sum(item.minutes for item in present)
