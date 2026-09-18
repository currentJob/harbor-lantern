"""여행 생성 · 조회 · 참여 · 상태 동기화 (REQ-001~003 · REQ-016).

`GET /state` 의 ETag 는 `trip.revision` 하나로 만든다. 그래서 **응답 본문이 서버의 현재
시각에 의존하면 안 된다** — 의존하는 순간 304 가 거짓말을 하고, 클라이언트는 바뀐 내용을
영영 못 보며, 에러 로그도 남지 않는다(§12 F1). 현지 시계·"지금 열려 있나"는 클라이언트 몫이다.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict

from harbor_lantern.api.deps import ClockDep, Conn, SettingsDep, TripAccessDep
from harbor_lantern.api.ratelimit import client_ip, enforce_join_rate_limit
from harbor_lantern.api.schemas import (
    ErrorBody,
    JoinRequest,
    JoinResponse,
    TripCreateRequest,
    TripCreateResponse,
    TripOut,
    TripStateOut,
)
from harbor_lantern.services import plan_service, trip_service
from harbor_lantern.services.itinerary_review import build_review

router = APIRouter(prefix="/api", tags=["trips"])

_NOT_FOUND = {404: {"model": ErrorBody, "description": "대상 없음 (또는 접근 권한 없음 — 구분하지 않는다)"}}


class ItineraryReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_reviews: bool = False


@router.post("/trips/{trip_id}/review-plan", responses=_NOT_FOUND, summary="전체 일정 점검·리뷰 기반 동선 제안")
def review_plan(body: ItineraryReviewRequest, access: TripAccessDep, settings: SettingsDep,
                request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return build_review(access.conn, access.trip, settings, request.app.state.discovery, body.use_reviews)


@router.post(
    "/trips",
    status_code=201,
    response_model=TripCreateResponse,
    summary="여행 생성 (27스팟 시드 자동 주입)",
)
def create_trip(
    conn: Conn,
    clock: ClockDep,
    settings: SettingsDep,
    payload: TripCreateRequest | None = None,
) -> dict[str, Any]:
    """4개 일자와 27개 스팟을 시드로 주입하고 초대코드·개설자 토큰을 발급한다
    (REQ-001 · REQ-002 · AC-001 · AC-002 · AC-003)."""
    body = payload if payload is not None else TripCreateRequest()
    return trip_service.create_trip(
        conn,
        clock,
        settings,
        name=body.name,
        start_date=body.start_date or settings.default_start_date,
        organizer_display_name=body.organizer_display_name,
    )


@router.get("/trips/{trip_id}", response_model=TripOut, responses=_NOT_FOUND, summary="여행 메타 조회")
def get_trip(access: TripAccessDep) -> dict[str, Any]:
    """같은 여행을 다시 조회해도 초대코드는 동일하다 (REQ-002 · AC-003)."""
    return plan_service.trip_payload(access.trip)


@router.post(
    "/join",
    response_model=JoinResponse,
    responses={
        **_NOT_FOUND,
        409: {"model": ErrorBody, "description": "같은 여행에 동일 표시명"},
        429: {"model": ErrorBody, "description": "시도 횟수 초과"},
    },
    summary="초대코드로 참여 (계정 없음)",
)
def join(
    request: Request,
    conn: Conn,
    clock: ClockDep,
    settings: SettingsDep,
    payload: JoinRequest,
) -> dict[str, Any]:
    """(REQ-003 · NFR-006 · AC-004 · AC-005 · AC-040)

    레이트 리밋은 **코드 유효성 검사보다 먼저**다. 그리고 형식이 틀린 코드와 존재하지
    않는 코드는 **완전히 같은 404 본문**이다 — 구별되는 순간 열거 공격의 신호가 된다.
    """
    ip = client_ip(request)
    enforce_join_rate_limit(conn, clock, settings, ip)
    return trip_service.join_trip(
        conn,
        clock,
        settings,
        ip=ip,
        invite_code=payload.invite_code,
        display_name=payload.display_name,
    )


@router.get(
    "/trips/{trip_id}/state",
    response_model=TripStateOut,
    responses={**_NOT_FOUND, 304: {"description": "변경 없음 (본문 없음)"}},
    summary="전체 상태 조회 (타임라인·경고·충돌·진행률 포함)",
)
def get_state(
    access: TripAccessDep,
    settings: SettingsDep,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Any:
    """(REQ-010~REQ-013 · REQ-016 · AC-035 · AC-036)

    응답은 `trip.revision` 만으로 결정된다 — **서버의 현재 시각에 의존하지 않는다.**
    그래서 ETag 를 리비전으로 만들 수 있다(§12 F1).
    """
    etag = f'"{int(access.trip["revision"])}"'
    if if_none_match is not None and _matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return plan_service.build_state(access.conn, access.trip, settings)


def _matches(header_value: str, etag: str) -> bool:
    """`If-None-Match` 는 목록이고 `W/` 접두어가 붙을 수 있다. `*` 는 항상 일치다."""
    candidates = [item.strip() for item in header_value.split(",")]
    for candidate in candidates:
        if candidate == "*":
            return True
        normalized = candidate[2:] if candidate.startswith("W/") else candidate
        if normalized == etag:
            return True
    return False


__all__ = ["router"]
