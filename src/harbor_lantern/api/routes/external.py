"""Cached public weather, exchange rates, and nearby places."""

from typing import Annotated

from fastapi import APIRouter, Query

from harbor_lantern.api.deps import FxProviderDep, NearbyProviderDep, WeatherProviderDep
from harbor_lantern.api.schemas import FxResponseOut, NearbyResponseOut, WeatherResponseOut
from harbor_lantern.config import (
    NEARBY_CATEGORIES,
    NEARBY_DEFAULT_RADIUS_M,
    NEARBY_MAX_RADIUS_M,
    NEARBY_MIN_RADIUS_M,
    enabled_nearby_categories,
)
from harbor_lantern.domain.geo import directions_url, haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.services.external.cache import CachedProvider

router = APIRouter(prefix="/api", tags=["external"])


def payload(provider: CachedProvider):
    result = provider.get()
    return {**(result.payload or {}), **result.as_meta()}


@router.get("/weather", response_model=WeatherResponseOut)
def weather(provider: WeatherProviderDep):
    return payload(provider)


@router.get("/fx", response_model=FxResponseOut)
def fx(provider: FxProviderDep):
    return payload(provider)


# 참가자 토큰을 요구하지 않는다 — 여행에 속한 데이터가 아니라 공개 지리 정보이고,
# weather/fx 와 같은 성격이다. 토큰을 요구하면 "가입 전에 근처를 둘러본다"가 막힌다.
@router.get("/nearby", response_model=NearbyResponseOut)
def nearby(
    provider: NearbyProviderDep,
    lat: Annotated[float, Query(ge=-90, le=90, description="현재 위치 위도")],
    lng: Annotated[float, Query(ge=-180, le=180, description="현재 위치 경도")],
    radius_m: Annotated[int, Query(
        ge=NEARBY_MIN_RADIUS_M, le=NEARBY_MAX_RADIUS_M, description="검색 반경(m)",
    )] = NEARBY_DEFAULT_RADIUS_M,
    category: Annotated[list[str] | None, Query(description="카테고리(생략 시 전체)")] = None,
):
    """현재 위치 기준 근처 장소 (REQ-017 · AC-050~AC-056).

    범위 밖 좌표·반경은 `Query` 제약이 422 로 막는다(AC-052 · AC-053). 꺼진 카테고리를
    요청해도 422 다 — 조용히 무시하면 사용자는 "결과가 없다"와 구분할 수 없다.
    """
    allowed = enabled_nearby_categories()
    requested = tuple(category) if category else allowed
    unknown = [name for name in requested if name not in allowed]
    if unknown:
        from fastapi import HTTPException
        raise HTTPException(422, detail={
            "error": "unknown_category",
            "message": f"지원하지 않는 카테고리입니다: {', '.join(unknown)}",
            "allowed": list(allowed),
        })

    result = provider.get(categories=requested, lat=lat, lng=lng, radius_m=radius_m)
    origin = LatLng(lat, lng)

    # 거리·길찾기 URL 은 **여기서** 만든다. 캐시 키는 좌표를 양자화하므로 거리를 캐시에
    # 넣으면 최대 110m 어긋난 값이 굳는다(`PlaceSnapshot` 주석).
    places = []
    for item in (result.payload or {}).get("places", []):
        distance = haversine_m(origin, LatLng(item["lat"], item["lng"]))
        places.append({
            **item,
            "category_label": NEARBY_CATEGORIES[item["category"]].label,
            "distance_m": distance,
            "directions_url": directions_url(item["lat"], item["lng"]),
        })
    places.sort(key=lambda p: (p["distance_m"], p["name"], p["osm_type"], p["osm_id"]))

    return {
        "origin_lat": lat, "origin_lng": lng, "radius_m": radius_m,
        "categories": list(requested), "places": places, **result.as_meta(),
    }
