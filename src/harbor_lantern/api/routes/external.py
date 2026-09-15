"""Cached public weather, exchange rates, and nearby places."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from harbor_lantern.api.deps import FxProviderDep, NearbyProviderDep, WeatherProviderDep
from harbor_lantern.api.schemas import (
    CuratedResponseOut,
    FxResponseOut,
    NearbyResponseOut,
    WeatherResponseOut,
)
from harbor_lantern.config import (
    NEARBY_CATEGORIES,
    NEARBY_DEFAULT_RADIUS_M,
    NEARBY_MAX_RADIUS_M,
    NEARBY_MIN_RADIUS_M,
    enabled_nearby_categories,
)
from harbor_lantern.domain.geo import directions_url, haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.services.curated import load_curated, select
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


@router.get("/curated", response_model=CuratedResponseOut)
def curated(
    city: Annotated[str | None, Query(pattern="^(HK|MO)$", description="HK · MO (생략 시 전체)")] = None,
    min_stars: Annotated[int, Query(ge=1, le=3, description="이 등급 이상만")] = 1,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    """미리 조사해 저장해 둔 미쉐린 홍콩·마카오 목록 (REQ-019 · AC-058~AC-061).

    외부 호출이 없다 — 저장소 안의 파일을 읽는다. `lat`·`lng` 를 **둘 다** 주면
    좌표가 있는 항목을 가까운 순으로 올린다(좌표 없는 항목은 뒤에 남는다).

    응답에 `sources` 와 `known_gaps` 를 **항상 함께 싣는다.** 이 목록은 크라우드
    평점이 아니라 미쉐린 등급이고, 좌표가 비어 있는 항목이 있으며, 빕구르망은
    빠져 있다 — 그 사실을 모르고 쓰면 "홍콩에 좋은 집이 97곳뿐"이라고 읽게 된다.
    """
    if (lat is None) != (lng is None):
        raise HTTPException(422, detail={
            "error": "incomplete_origin",
            "message": "거리순으로 보려면 lat 과 lng 를 함께 주세요.",
        })

    dataset = load_curated()
    origin = LatLng(lat, lng) if lat is not None and lng is not None else None
    rows = select(dataset, city=city, min_stars=min_stars, origin=origin)
    return {
        "dataset": dataset.dataset,
        "retrieved_at": dataset.retrieved_at,
        "what_this_is": dataset.what_this_is,
        "sources": list(dataset.sources),
        "known_gaps": list(dataset.known_gaps),
        "counts": dataset.counts,
        "returned": len(rows),
        "places": rows,
    }


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
