"""거리 계산과 표기 — DSN-14 일부 (설계서 §6.3 · REQ-010 · AC-019 · AC-032 · AC-034).

참조 HTML 의 `dist()`·`fmt()` 를 **같은 값이 나오게** 옮긴 것이다. 같은 규칙이
프론트(`web/js/geo.js`)에도 존재하므로(현재 위치 기준 거리는 클라이언트 계산 · §6.15)
두 구현이 갈라지면 화면과 API 가 다른 거리를 말한다. AC-032 를 양쪽에서 검증한다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.util import round_half_up

__all__ = [
    "EARTH_RADIUS_M",
    "directions_url",
    "format_distance",
    "haversine_m",
    "path_length_m",
]

# 참조 HTML 의 `dist()` 와 같은 값 (구면 근사 반지름).
EARTH_RADIUS_M = 6_371_000.0

_DEG_TO_RAD = math.pi / 180.0


def haversine_m(a: LatLng, b: LatLng) -> float:
    """두 좌표 사이 대권거리(미터). 구면 근사이며 타원체 보정을 하지 않는다.

    `asin` 인자를 1.0 으로 자르는 이유: 대척점 근처에서 부동소수 오차로
    `sqrt(x)` 가 1 을 아주 조금 넘으면 `math.asin` 이 `ValueError` 를 던진다.
    홍콩 안에서는 일어나지 않지만, 사용자가 좌표를 편집할 수 있으므로(REQ-004)
    입력을 신뢰하지 않는다.
    """
    d_lat = (b.lat - a.lat) * _DEG_TO_RAD
    d_lng = (b.lng - a.lng) * _DEG_TO_RAD
    x = (
        math.sin(d_lat / 2.0) ** 2
        + math.cos(a.lat * _DEG_TO_RAD) * math.cos(b.lat * _DEG_TO_RAD) * math.sin(d_lng / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(x)))


def path_length_m(coords: Sequence[LatLng]) -> float:
    """연속한 좌표들을 잇는 총 거리(미터). 귀환 구간은 더하지 않는다(§6.16 목적함수).

    설계서 §6.3 에는 없는 헬퍼다. `route.optimize_day` 와 `travel.day_totals` 가
    같은 정의를 각자 들고 있으면 언젠가 한쪽만 바뀐다.
    """
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def format_distance(m: float) -> str:
    """참조 HTML `fmt()` 와 동일 규칙 (AC-032).

    `m<1000` → 10m 단위 `'NNNm'` · `m<10000` → 소수 1자리 `'N.Nkm'` · 그 이상 → 정수 `'Nkm'`.

    JS `Math.round`·`toFixed` 는 0.5 를 **위로** 올린다. 파이썬 내장 `round()`·`format` 은
    은행가 반올림이라 `1250m` 이 JS 에서는 `'1.3km'`, 파이썬 `f"{1.25:.1f}"` 에서는
    `'1.2'` 가 된다 — 그래서 전부 `round_half_up` 으로 자릿수를 먼저 정수로 만든 뒤
    문자열을 조립한다(설계서 §12 F4).
    """
    if m < 1000.0:
        return f"{round_half_up(m / 10.0) * 10}m"
    if m < 10000.0:
        tenths = round_half_up(m / 100.0)  # 100m = 0.1km 단위
        return f"{tenths // 10}.{tenths % 10}km"
    return f"{round_half_up(m / 1000.0)}km"


def directions_url(lat: float, lng: float) -> str:
    """구글맵 길찾기 딥링크 (AC-034). 참조 HTML 과 **문자열까지 동일**해야 한다.

    파이썬 `repr(float)` 은 JS 의 숫자 문자열화와 같은 "최단 왕복 표현"이라
    `114.1730` 이 양쪽 모두 `114.173` 이 된다. `:.4f` 같은 포맷으로 바꾸면
    좌표 문자열이 갈라진다 — AC-034 가 좌표 일치를 assert 한다.

    한 가지만 다르다: 정수값 좌표에서 파이썬은 `22.0`, JS 는 `22` 를 낸다.
    시드 27건에는 정수 좌표가 없고, 있어도 구글맵이 같은 지점으로 해석한다.
    """
    return f"https://www.google.com/maps/dir/?api=1&destination={lat!r},{lng!r}"
