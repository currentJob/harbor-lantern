"""Global travel planning, separate from the existing Hong Kong shared itinerary.

**두 경로가 한 표면에 있다** (설계서 §16.16 · DSN-45).

- **가이드 기본 경로** — 구운 도시 파일(`seed/city-guides/`)을 읽어 만든다. 외부 호출이 **0건**이라
  `execute()`(세마포어 · 503 변환)를 타지 않는다. 그 기계는 외부 공급자를 위한 것이고,
  파일 읽기에 씌우면 "혼잡" 이라는 거짓말이 가능해진다(NFR-017 · AC-079).
- **폴백 경로** — 굽지 않은 도시. 기존 Overpass 휴리스틱 그대로이고 **코드 한 줄도 바꾸지
  않았다**(A14 · AC-078). 더하는 것은 "제한된 자동 추천"이라는 표시뿐이다(REQ-028 · AC-077).

`use_reviews=true`는 두 경로에서 명시적으로 선택하는 확장이다. 제한된 리뷰 조회를 추가하고
확인한 평점만 순위에 반영한다. 기본값 false는 기존 오프라인 가이드 동작을 유지한다.

**`guide_grade` 는 두 경로 모두에서 반드시 실린다**(AC-085). 비어 있으면 화면이 아무 문구도
고르지 못하고, 사용자는 조사된 가이드와 자동 추천을 구분할 수 없게 된다 — 이 확장이 고치려는
바로 그 문제다.
"""

import math
import threading
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, model_validator

from harbor_lantern.api.schemas import GuideCityDetailOut, GuideListOut
from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.guide import build_guide_plan
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.planner import build_plan, opening_windows, travel_minutes
from harbor_lantern.domain.util import parse_hhmm
from harbor_lantern.services.external.ports import ExternalUnavailable
from harbor_lantern.services.external.reviews import collect_reviews, review_summary
from harbor_lantern.services.guides import CITY_ID_PATTERN, CityGuide, find_cities, load_city, load_index
from harbor_lantern.services.ratings import city_ratings, enrich_viewport, rated_spots

router = APIRouter(prefix="/api/explore", tags=["explore"])
_slots = threading.BoundedSemaphore(2)
_errors = {429: {"description": "조회가 진행 중입니다"}, 503: {"description": "장소 제공자 연결 실패"}}
_not_baked = {404: {"description": "아직 조사되지 않은 도시입니다"}}

# 세 등급의 문구는 **서로 달라야 한다** (AC-085 · §16.17). 같은 문구를 돌려쓰면 등급을
# 실어 보내는 의미가 없어진다 — 사용자가 화면에서 구분하지 못한다.
GRADE_NOTICE = {
    "full": "완전 가이드 · 조사 시점 {retrieved_at} — 주요 명소와 한국어 설명을 갖춘 도시입니다.",
    "partial": (
        "부분 가이드 · 조사 시점 {retrieved_at} — 조사된 장소와 설명이 일부뿐입니다. 빠진 곳이 있을 수 있습니다."
    ),
    "heuristic": (
        "제한된 자동 추천 — 조사된 도시 가이드가 없어 지도 데이터로 자동 구성했습니다. "
        "설명·팁·출처는 제공되지 않습니다."
    ),
}
GUIDES_NOTICE = "조사 시점에 구운 고정 데이터입니다. 조회 시 외부 요청을 하지 않습니다."
NO_MATCH_NOTICE = (
    "조건에 맞는 조사된 도시가 없습니다. 지명 검색으로 목적지를 직접 지정하면 제한된 자동 추천을 받을 수 있습니다."
)
NOT_BAKED_MESSAGE = (
    "아직 조사되지 않은 도시입니다. 목적지 좌표를 함께 보내면 제한된 자동 추천을 만들 수 있습니다."
)


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=400)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)


class PlanRequest(BaseModel):
    """`city_id` 와 `destination` 중 **하나는 필수**다 (설계서 §16.16).

    `destination` 을 선택으로 완화했지만 기존 요청은 그대로 통한다 — 더한 것은 `city_id`
    하나뿐이고, 둘 다 없는 요청만 새로 422 가 된다(예전에도 `destination` 없이는 422 였다).
    """

    model_config = ConfigDict(extra="forbid")
    city_id: str | None = Field(default=None, max_length=64, pattern=CITY_ID_PATTERN)
    destination: Destination | None = None
    start_date: date
    end_date: date
    pace: Literal["relaxed", "balanced", "full"] = "balanced"
    interests: Literal["mixed", "culture", "nature"] = "mixed"
    radius_m: int = Field(default=5000, ge=500, le=10000)
    use_reviews: bool = False
    use_ratings: bool = True

    @model_validator(mode="after")
    def dates(self):
        if not 0 <= (self.end_date - self.start_date).days <= 13:
            raise ValueError("여행 기간은 시작일부터 1~14일이어야 합니다.")
        return self

    @model_validator(mode="after")
    def target(self):
        if not self.city_id and self.destination is None:
            raise ValueError("city_id 또는 destination 중 하나는 필요합니다.")
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


def guides_dir(request: Request) -> str | None:
    """테스트가 가이드 디렉터리를 갈아끼우는 자리. 운영에서는 `None`(= 기본 `seed/city-guides/`).

    `getattr` 기본값으로 읽는 이유는 앱이 이 상태를 **설정하지 않아도** 돌아야 하기
    때문이다 — 아직 굽지 않은 저장소에서도 서버는 뜬다.
    """
    return getattr(request.app.state, "guides_dir", None)


def grade_of(value: str) -> str:
    """등급을 화면이 쓸 수 있는 세 값 중 하나로 읽는다 (AC-085).

    모르는 값·빈 값은 **'부분'으로 낮춰 읽는다**. 올려 읽으면(= `full`) 조사되지 않은
    빈 곳을 "완전"이라고 말하게 되고, 비워 두면 화면이 아무 문구도 고르지 못한다.
    """
    return value if value in GRADE_NOTICE else "partial"


@router.get("/guides", response_model=GuideListOut)
def guides(
    request: Request,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    country: Annotated[str | None, Query(min_length=2, max_length=3, pattern="^[A-Za-z]{2,3}$")] = None,
) -> dict[str, Any]:
    """구운 도시 목록 — REQ-027 (§16.15 · AC-075 · AC-076).

    **`execute()` 를 타지 않는다** — 외부 호출이 0건이므로 세마포어도 503 변환도 필요 없다.
    결과가 비어도 200 이다(AC-076).
    """
    index = load_index(guides_dir(request))
    cities = find_cities(q, country, index)
    return {
        "cities": [{**c, "rated_count": len(city_ratings(c["city_id"]))} for c in cities],
        "counts": index.counts,
        "sources": list(index.sources),
        "known_gaps": list(index.known_gaps),
        "retrieved_at": index.retrieved_at,
        "notice": GUIDES_NOTICE if cities else NO_MATCH_NOTICE,
    }


@router.get("/guides/{city_id}", responses=_not_baked, response_model=GuideCityDetailOut)
def guide_detail(
    request: Request,
    city_id: Annotated[str, PathParam(max_length=64, pattern=CITY_ID_PATTERN)],
) -> dict[str, Any]:
    """도시 하나의 가이드 — REQ-022 · REQ-024. 굽지 않은 도시는 404 다."""
    guide = load_city(city_id, guides_dir(request))
    if guide is None:
        raise HTTPException(404, NOT_BAKED_MESSAGE)
    grade = grade_of(guide.grade)
    return {
        "city_id": guide.city_id,
        "name_ko": guide.name_ko,
        "name_local": guide.name_local,
        "name_en": guide.name_en,
        "country_code": guide.country_code,
        "country_ko": guide.country_ko,
        "country_en": guide.country_en,
        "center": guide.center,
        "grade": grade,
        "spot_count": len(guide.spots),
        "retrieved_at": guide.retrieved_at,
        "radius_m": guide.radius_m,
        "harvest": guide.harvest,
        "sources": list(guide.sources),
        "known_gaps": list(guide.known_gaps),
        "spots": rated_spots(guide.city_id, guide.spots),
        "notice": GRADE_NOTICE[grade].format(retrieved_at=guide.retrieved_at),
    }


def guided_plan(guide: CityGuide, body: PlanRequest, provider=None) -> dict[str, Any]:
    """구운 도시로 만든 일정. use_reviews=False인 기본 경로는 외부 호출 0건이다."""
    grade = grade_of(guide.grade)
    spots = rated_spots(guide.city_id, guide.spots) if body.use_ratings else guide.spots
    evidence = None
    if body.use_reviews and provider is not None:
        evidence = collect_reviews(provider, list(spots), True)
        spots = [{**s, "review": evidence[s["id"]] if evidence[s["id"]]["status"] == "matched"
                  else s.get("review", evidence[s["id"]])} for s in spots]
    result = build_guide_plan(
        {**guide.as_city(), "grade": grade},
        spots,
        body.start_date,
        body.end_date,
        body.pace,
        body.interests,
    )
    result["guide_notice"] = GRADE_NOTICE[grade].format(retrieved_at=guide.retrieved_at)
    if evidence is not None:
        result["review_summary"] = review_summary(evidence)
        result["notice"] += (" 리뷰 확인 장소만 평가 수 보정 점수로 우선순위를 조정했습니다."
                             if result["review_summary"]["counts"].get("matched") else
                             " 리뷰를 확인하지 못해 기존 가이드 우선순위를 사용했습니다.")
    result["rating_summary"] = {
        "source": "Trip.com", "matched": sum(
            s.get("review", {}).get("status") == "matched"
            and s.get("review", {}).get("source") == "Trip.com" for s in spots),
        "enabled": body.use_ratings, "notice": "평점과 리뷰 수를 보정한 조사 시점 자료입니다. 실시간 평점이 아닙니다.",
    }
    result["guide_city"] = {
        "city_id": guide.city_id,
        "name_ko": guide.name_ko,
        "grade": grade,
        "retrieved_at": guide.retrieved_at,
        "harvest": guide.harvest,
        "sources": list(guide.sources),
        "known_gaps": list(guide.known_gaps),
    }
    return result


@router.post("/plan", responses=_errors)
def plan(body: PlanRequest, request: Request, response: Response):
    if body.use_reviews:
        response.headers["Cache-Control"] = "no-store"
    if body.city_id:
        guide = load_city(body.city_id, guides_dir(request))
        if guide is not None:
            return guided_plan(guide, body, request.app.state.discovery)
        if body.destination is None:
            # 굽지 않은 도시 + 좌표 없음 = 폴백조차 만들 수 없다. 422 다 — 요청이 부족한 것이지
            # 서버가 고장난 것이 아니다.
            raise HTTPException(422, NOT_BAKED_MESSAGE)

    destination = body.destination.model_dump()
    places = execute(lambda: request.app.state.discovery.places(
        destination["lat"], destination["lng"], body.radius_m))
    # Overpass can return way centers outside the requested circle.
    origin = LatLng(destination["lat"], destination["lng"])
    places = [p for p in places if haversine_m(origin, LatLng(p["lat"], p["lng"])) <= body.radius_m]
    evidence = None
    if body.use_reviews:
        evidence = collect_reviews(request.app.state.discovery, places, True)
        places = [{**p, "review": evidence[p["id"]]} for p in places]
    result = build_plan(places, destination, body.start_date, body.end_date, body.pace, body.interests)
    if evidence is not None:
        result["review_summary"] = review_summary(evidence)
    if any(p.get("limited_search") for p in places):
        result["notice"] += " 제공자 혼잡으로 대체 검색의 일부 주요 후보를 사용했습니다."
    # 기본 폴백은 기존 계산을 유지하며, 명시적인 리뷰 옵션만 순위에 영향을 준다.
    result["guide_grade"] = "heuristic"
    result["guide_notice"] = GRADE_NOTICE["heuristic"]
    result["guide_city"] = None
    return result


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
    notice = "평점·후기는 Google Maps 제공 시 표시합니다. 미제공 정보는 추정하지 않습니다."
    if any(p.get("limited_search") for p in places):
        notice += " 제공자 혼잡으로 대체 검색 결과 일부를 표시합니다."
    return {"places": results[:30], "reviews_enabled": provider.reviews_enabled, "notice": notice}


class EditablePlace(Destination):
    model_config = ConfigDict(extra="allow")
    id: str = Field(max_length=120)
    hours_text: str = Field(default="", max_length=2000)
    opening_hours: str = Field(default="", max_length=2000)


class EditableStop(BaseModel):
    place: EditablePlace
    duration: int = Field(default=90, ge=5, le=720)
    completed: bool = False
    fixed_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class EditableDay(BaseModel):
    date: date
    title: str = Field(default="", max_length=200)
    area: str = Field(default="", max_length=200)
    color: str = Field(default="#245548", pattern=r"^#[0-9a-fA-F]{6}$")
    stops: list[EditableStop] = Field(max_length=40)
    start_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class InsertStop(BaseModel):
    day_index: int = Field(ge=0, le=13)
    stop: EditableStop


class RecalculateRequest(BaseModel):
    days: list[EditableDay] = Field(min_length=1, max_length=14)
    insert: InsertStop | None = None


@router.post("/recalculate", responses={409: {"description": "이미 추가된 장소"}})
def recalculate(body: RecalculateRequest):
    """Recompute a user's explicit order without dropping or silently rearranging stops."""
    if body.insert is not None:
        insertion = body.insert
        if insertion.day_index >= len(body.days):
            raise HTTPException(422, "추가할 날짜가 없습니다.")
        day = body.days[insertion.day_index]
        if len(day.stops) >= 40:
            raise HTTPException(422, "하루 최대 40곳까지 추가할 수 있습니다.")
        place = insertion.stop.place
        for stop in day.stops:
            old = stop.place
            old_wd = old.model_extra.get("wikidata_id") or (old.id[3:] if old.id.startswith("wd:") else None)
            new_wd = place.model_extra.get("wikidata_id") or (place.id[3:] if place.id.startswith("wd:") else None)
            if (old.id == place.id or (old_wd and old_wd == new_wd)
                    or (old.name.casefold() == place.name.casefold()
                        and haversine_m(LatLng(old.lat, old.lng), LatLng(place.lat, place.lng)) < 50)):
                raise HTTPException(409, "이 날짜에 이미 추가된 장소입니다.")
        first = max((i + 1 for i, s in enumerate(day.stops) if s.completed), default=0)
        # ponytail: exact best insertion in a fixed order (<=41 slots); no road-network/TSP solver.
        candidates = []
        for index in range(first, len(day.stops) + 1):
            candidate = day.model_copy(update={"stops": day.stops[:index] + [insertion.stop] + day.stops[index:]})
            computed = recalculate(RecalculateRequest(days=[candidate]))["days"][0]
            score = (sum(len(s["warnings"]) for s in computed["stops"]), computed["distance_m"], index)
            candidates.append((score, index, candidate))
        _, index, chosen = min(candidates, key=lambda c: c[0])
        days = list(body.days)
        days[insertion.day_index] = chosen
        result = recalculate(RecalculateRequest(days=days))
        result["insertion"] = {"day_index": insertion.day_index, "stop_index": index,
                               "notice": "시간 충돌이 적고 예상 이동거리가 가장 짧은 위치에 추가했습니다. "
                                         "기존 순서는 유지하며 실제 도로 최단 경로는 아닙니다."}
        return result
    result = []
    for day in body.days:
        cursor, previous, stops = parse_hhmm(day.start_time), None, []
        for stop in day.stops:
            place = stop.place.model_dump()
            position = LatLng(place["lat"], place["lng"])
            distance = round(haversine_m(previous, position)) if previous else 0
            travel = travel_minutes(distance) if previous else 0
            eta = cursor + travel
            windows = opening_windows(place.get("hours_text") or place.get("opening_hours"), day.date.weekday())
            warnings = []
            status = "unverified"
            if stop.fixed_start:
                fixed = parse_hhmm(stop.fixed_start)
                if eta > fixed:
                    warnings.append(f"지정 시각 {stop.fixed_start}보다 약 {eta - fixed}분 늦게 도착합니다.")
                eta = max(eta, fixed)
            if windows is not None:
                feasible = [max(eta, a) for a, b in windows if max(eta, a) + stop.duration <= b
                            and (not stop.fixed_start or a <= eta)]
                if feasible:
                    eta = min(feasible)
                    status = "weekly_hours"
                else:
                    warnings.append("예상 방문·체류 시간이 영업시간 밖입니다. 순서나 날짜를 조정하세요.")
            end = eta + stop.duration
            if end >= 24 * 60:
                warnings.append("일정이 다음 날까지 이어집니다. 장소를 다른 날로 옮겨 주세요.")
            def hhmm(value):
                return f"{value // 60:02}:{value % 60:02}"
            stops.append({"place": place, "arrival": hhmm(eta), "departure": hhmm(end),
                          "fixed_start": stop.fixed_start,
                          "duration": stop.duration, "completed": stop.completed, "warnings": warnings,
                          "travel_minutes": travel, "distance_m": distance, "hours_status": status})
            previous, cursor = position, end
        result.append({"date": day.date.isoformat(), "weekday": day.date.weekday(), "title": day.title,
                       "start_time": day.start_time,
                       "area": day.area, "color": day.color, "stops": stops,
                       "distance_m": sum(s["distance_m"] for s in stops),
                       "travel_minutes": sum(s["travel_minutes"] for s in stops)})
    return {"days": result, "scheduled_count": sum(len(d["stops"]) for d in result)}


@router.get("/places/search", responses=_errors)
def search_places(request: Request, q: Annotated[str, Query(min_length=2, max_length=100)],
                  lat: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
                  lng: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)]):
    if len(q.strip()) < 2:
        raise HTTPException(422, "장소 이름을 두 글자 이상 입력하세요.")
    return {"items": execute(lambda: request.app.state.discovery.search_places(q.strip(), lat, lng)),
            "attribution": "© OpenStreetMap contributors", "notice": "도시 중심 50km 이내 검색 결과입니다."}


@router.get('/places/viewport', responses=_errors)
def viewport_places(request: Request,
                    south: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
                    west: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
                    north: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
                    east: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
                    category: Literal['all', 'attraction', 'culture', 'nature', 'history',
                                      'religion', 'food', 'shopping'] = 'all'):
    width = east-west if east >= west else 360+east-west
    area = (north-south)*width*111**2*math.cos(math.radians((south+north)/2))
    if not 0 < north-south <= 1 or not 0 < width <= 1 or area > 1200:
        raise HTTPException(422, '지도를 더 확대해 주세요. 한 번에 약 1,200km² 이내를 조회합니다.')
    bounds = (south, west, north, east)
    try:
        result = execute(lambda: request.app.state.discovery.viewport(bounds, category))
        items = enrich_viewport(result['items'], bounds, guides_dir(request))
        return {**result, 'items': items, 'partial': False, 'bounds': bounds,
                'notice': ('현재 영역의 공개 지도 장소입니다. 등록되지 않은 장소는 빠질 수 있으며 '
                           '평점은 확인된 조사 자료만 표시합니다.'),
                'attribution': '© OpenStreetMap contributors / Wikidata · Trip.com 조사 시점 평점'}
    except HTTPException as exc:
        if exc.status_code not in (429, 503):
            raise
        return {'items': enrich_viewport([], bounds, guides_dir(request)), 'partial': True,
                'truncated': False, 'limit': 500, 'bounds': bounds,
                'notice': f'{exc.detail} 현재는 조사된 장소만 표시합니다. 다시 조회해 주세요.',
                'attribution': 'Wikidata / Trip.com 조사 자료'}
