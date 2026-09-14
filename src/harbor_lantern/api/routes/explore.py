"""Global travel planning, separate from the existing Hong Kong shared itinerary."""

import threading
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.planner import build_plan
from harbor_lantern.services.external.ports import ExternalUnavailable

router = APIRouter(prefix="/api/explore", tags=["explore"])
_slots = threading.BoundedSemaphore(2)
_errors = {429: {"description": "조회가 진행 중입니다"}, 503: {"description": "장소 제공자 연결 실패"}}


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=400)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: Destination
    start_date: date
    end_date: date
    pace: Literal["relaxed", "balanced", "full"] = "balanced"
    interests: Literal["mixed", "culture", "nature"] = "mixed"
    radius_m: int = Field(default=5000, ge=500, le=10000)

    @model_validator(mode="after")
    def dates(self):
        if not 0 <= (self.end_date - self.start_date).days <= 13:
            raise ValueError("여행 기간은 시작일부터 1~14일이어야 합니다.")
        return self


def execute(call):
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "다른 장소 조회가 진행 중입니다. 잠시 후 다시 시도하세요.")
    try:
        return call()
    except ExternalUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        _slots.release()


@router.get("/destinations", responses=_errors)
def destinations(request: Request, q: Annotated[str, Query(min_length=2, max_length=100)]):
    return {"items": execute(lambda: request.app.state.discovery.destinations(q.strip())),
            "attribution": "© OpenStreetMap contributors"}


@router.post("/plan", responses=_errors)
def plan(body: PlanRequest, request: Request):
    destination = body.destination.model_dump()
    places = execute(lambda: request.app.state.discovery.places(
        destination["lat"], destination["lng"], body.radius_m))
    # Overpass can return way centers outside the requested circle.
    origin = LatLng(destination["lat"], destination["lng"])
    places = [p for p in places if haversine_m(origin, LatLng(p["lat"], p["lng"])) <= body.radius_m]
    return build_plan(places, destination, body.start_date, body.end_date, body.pace, body.interests)


@router.get("/nearby", responses=_errors)
def nearby(request: Request, lat: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
           lng: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
           radius_m: Annotated[int, Query(ge=100, le=3000)] = 800):
    provider = request.app.state.discovery
    def fetch():
        reviewed = provider.reviewed_restaurants(lat, lng, radius_m)
        return reviewed if reviewed is not None else provider.places(lat, lng, radius_m, restaurants_only=True)
    places = execute(fetch)
    origin = LatLng(lat, lng)
    results = [{**p, "distance_m": round(haversine_m(origin, LatLng(p["lat"], p["lng"])))} for p in places]
    results = [p for p in results if p["distance_m"] <= radius_m]
    results.sort(key=lambda p: (p["distance_m"], p["name"]))
    return {"places": results[:30], "reviews_enabled": provider.reviews_enabled,
            "notice": "평점·후기는 Google Maps 제공 시 표시합니다. 미제공 정보는 추정하지 않습니다."}
