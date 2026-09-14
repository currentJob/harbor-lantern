"""Authenticated itinerary endpoints.

`responses=` 로 선언한 실패 코드는 장식이 아니다 — `tests/api/test_openapi_contract.py`
가 이 집합을 `contracts/openapi.yaml` 과 **정확히** 대조한다. 한쪽만 바꾸면 실패한다.

`PATCH` 는 본문의 `version`(리소스 낙관적 잠금), 재정렬·이동은 `expected_revision`
(여행 동기화)을 받는다. 둘 다 정수라 바꿔 써도 타입 검사가 안 잡는다(§12 F3).
"""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from harbor_lantern.api import schemas as s
from harbor_lantern.api.deps import ClockDep, SettingsDep, TripAccessDep
from harbor_lantern.services import spot_service as service

router = APIRouter(prefix="/api/trips/{trip_id}", tags=["spots"])

_NOT_FOUND = {404: {"model": s.ErrorBody, "description": "대상 없음 (또는 접근 권한 없음 — 구분하지 않는다)"}}
_VERSION_CONFLICT = {409: {"model": s.ErrorBody, "description": "version 불일치 — 최신 리소스 동봉"}}
_REVISION_CONFLICT = {409: {"model": s.ErrorBody, "description": "expected_revision 불일치 — 최신 상태 동봉"}}

# 계약(`contracts/openapi.yaml` 의 `DayIndex`)이 1~4 로 못박았다. 제약을 여기 걸어
# 두지 않으면 범위 밖 번호가 서비스까지 내려가 404 가 되는데, 그러면 "없는 여행"과
# "잘못 쓴 번호"가 같은 응답이 된다 — 계약은 후자를 422 라고 적었다.
DayIndex = Annotated[int, Path(ge=1, le=4)]


@router.post("/days/{day_index}/spots", status_code=201, response_model=s.SpotOut, responses={**_NOT_FOUND})
def create(day_index: DayIndex, body: s.SpotCreateRequest, access: TripAccessDep,
           clock: ClockDep, settings: SettingsDep):
    return service.create_spot(access.conn, clock, settings, trip=access.trip,
                               day_index=day_index, fields=body.model_dump())


@router.patch("/spots/{spot_id}", response_model=s.SpotOut, responses={**_NOT_FOUND, **_VERSION_CONFLICT})
def update(spot_id: str, body: s.SpotUpdateRequest, access: TripAccessDep, clock: ClockDep, settings: SettingsDep):
    return service.update_spot(access.conn, clock, settings, trip=access.trip,
                               spot_id=spot_id, version=body.version, changes=body.changes())


@router.delete("/spots/{spot_id}", status_code=204, responses={**_NOT_FOUND})
def delete(spot_id: str, access: TripAccessDep, clock: ClockDep):
    service.delete_spot(access.conn, clock, trip=access.trip, spot_id=spot_id)
    return Response(status_code=204)


@router.put("/days/{day_index}/order", response_model=s.DayOrderResponse,
            responses={**_NOT_FOUND, **_REVISION_CONFLICT})
def reorder(day_index: DayIndex, body: s.ReorderRequest, access: TripAccessDep, clock: ClockDep):
    return service.reorder_day(access.conn, clock, trip=access.trip, day_index=day_index,
                               expected_revision=body.expected_revision, spot_ids=body.spot_ids)


@router.post("/spots/{spot_id}/move", response_model=s.MoveResponse,
             responses={**_NOT_FOUND, **_REVISION_CONFLICT})
def move(spot_id: str, body: s.MoveRequest, access: TripAccessDep, clock: ClockDep):
    return service.move_spot(access.conn, clock, trip=access.trip, spot_id=spot_id, **body.model_dump())


@router.put("/spots/{spot_id}/done", response_model=s.DoneResponse, responses={**_NOT_FOUND})
def done(spot_id: str, body: s.DoneRequest, access: TripAccessDep, clock: ClockDep):
    return service.set_done(access.conn, clock, trip=access.trip, spot_id=spot_id,
                            done=body.done, participant=access.participant)


@router.post("/days/{day_index}/optimize", response_model=s.RouteProposalOut, responses={**_NOT_FOUND})
def optimize(day_index: DayIndex, access: TripAccessDep):
    return service.optimize_day(access.conn, trip=access.trip, day_index=day_index)
