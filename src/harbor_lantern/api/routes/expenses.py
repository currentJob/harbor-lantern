"""Authenticated expense and settlement endpoints."""

from fastapi import APIRouter, Response

from harbor_lantern.api import schemas as s
from harbor_lantern.api.deps import ClockDep, FxProviderDep, TripAccessDep
from harbor_lantern.api.routes.external import payload
from harbor_lantern.services import expense_service as service

router = APIRouter(prefix="/api/trips/{trip_id}", tags=["expenses"])

_NOT_FOUND = {404: {"model": s.ErrorBody, "description": "대상 없음 (또는 접근 권한 없음 — 구분하지 않는다)"}}
_VERSION_CONFLICT = {409: {"model": s.ErrorBody, "description": "version 불일치 — 최신 리소스 동봉"}}


@router.get("/expenses", response_model=s.ExpenseListOut, responses={**_NOT_FOUND})
def list_expenses(access: TripAccessDep, provider: FxProviderDep):
    fx = payload(provider)
    return service.list_expenses(access.conn, str(access.trip["id"]), rate_micro=fx.get("rate_micro"), fx_payload=fx)


@router.post("/expenses", status_code=201, response_model=s.ExpenseOut, responses={**_NOT_FOUND})
def create(body: s.ExpenseCreateRequest, access: TripAccessDep, clock: ClockDep, provider: FxProviderDep):
    return service.create_expense(access.conn, clock, trip=access.trip,
                                   rate_micro=payload(provider).get("rate_micro"),
                                   **body.model_dump(exclude={"currency"}))


@router.patch("/expenses/{expense_id}", response_model=s.ExpenseOut,
              responses={**_NOT_FOUND, **_VERSION_CONFLICT})
def update(expense_id: str, body: s.ExpenseUpdateRequest, access: TripAccessDep, provider: FxProviderDep):
    return service.update_expense(access.conn, trip=access.trip, expense_id=expense_id,
                                   version=body.version, changes=body.changes(),
                                   share_participant_ids=body.share_participant_ids,
                                   rate_micro=payload(provider).get("rate_micro"))


@router.delete("/expenses/{expense_id}", status_code=204, responses={**_NOT_FOUND})
def delete(expense_id: str, access: TripAccessDep):
    service.delete_expense(access.conn, trip=access.trip, expense_id=expense_id)
    return Response(status_code=204)


@router.get("/settlement", response_model=s.SettlementOut, responses={**_NOT_FOUND})
def settlement(access: TripAccessDep):
    return service.build_settlement(access.conn, str(access.trip["id"]))
