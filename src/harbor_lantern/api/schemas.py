"""Pydantic 요청/응답 모델 — `contracts/openapi.yaml` 이 SSoT 다.

여기와 계약이 어긋나면 `tests/api/test_openapi_contract.py` 가 잡는다. 계약을 바꾸려면
**두 곳을 같이** 바꿔야 한다 — 그것이 목적이다(설계서 §7.1).

요청 모델은 전부 `extra="forbid"` 다(계약의 `additionalProperties: false`). 오타 난 필드를
조용히 무시하면 "고쳤는데 안 바뀐다"가 되고, 그건 사용자가 서버를 불신하게 만든다.

**표시명·초대코드는 Pydantic 제약을 걸지 않는다.** 참여(`POST /api/join`)는 검사 순서가
곧 보안 속성이라 레이트 리밋이 **가장 먼저** 와야 하는데(§6.17), Pydantic 검증은 핸들러
이전에 돌아 그 순서를 깨 버린다. 그래서 길이 검사는 서비스 계층이 한다(같은 422).
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "BalanceOut",
    "ConflictOut",
    "DayOrderResponse",
    "DayStateOut",
    "DoneRequest",
    "DoneResponse",
    "DoneStateOut",
    "ErrorBody",
    "ExpenseCreateRequest",
    "ExpenseListOut",
    "ExpenseOut",
    "ExpenseUpdateRequest",
    "FxResponseOut",
    "HealthOut",
    "HoursSpecOut",
    "JoinRequest",
    "JoinResponse",
    "LegOut",
    "MoveRequest",
    "MoveResponse",
    "ParticipantOut",
    "ProgressOut",
    "ReorderRequest",
    "RouteProposalOut",
    "ScheduleOut",
    "SettlementOut",
    "SpotCreateRequest",
    "SpotInDayOut",
    "SpotOut",
    "SpotUpdateRequest",
    "TransferOut",
    "TripCreateRequest",
    "TripCreateResponse",
    "TripOut",
    "TripStateOut",
    "WarningOut",
    "WeatherResponseOut",
]

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
DwellMinutes = Annotated[int, Field(ge=0, le=1440)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── 공통 ──────────────────────────────────────────────────────────────────
class ErrorBody(BaseModel):
    """모든 에러의 유일한 모양 (설계서 §7.2)."""

    error: str
    message: str
    detail: dict[str, Any] | None = None


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str


class ProgressOut(BaseModel):
    done: int
    total: int
    percent: int


# ── trip ──────────────────────────────────────────────────────────────────
class TripCreateRequest(Request):
    name: str = Field(default="홍콩 3박 4일", min_length=1, max_length=80)
    start_date: str | None = Field(
        default=None,
        description="미지정 시 설정값(기본 2026-10-05 · 가정 A2). Day 1~4 가 이 날짜부터 연속 배정된다.",
    )
    organizer_display_name: str = "개설자"

    @field_validator("start_date")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date_type.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("start_date 는 'YYYY-MM-DD' 여야 합니다") from exc


class TripOut(BaseModel):
    id: str
    name: str
    start_date: str
    base_currency: Literal["HKD"]
    invite_code: str
    invite_code_display: str
    revision: int
    created_at: str


class ParticipantOut(BaseModel):
    id: str
    display_name: str
    is_organizer: bool
    joined_at: str


class TripCreateResponse(BaseModel):
    trip: TripOut
    participant: ParticipantOut
    participant_token: str


class JoinRequest(Request):
    invite_code: str = Field(description="대소문자·하이픈·공백 무관. 서버가 정규화한다 (I·L→1, O→0).")
    display_name: str = Field(description="1~24자. 길이 검사는 서비스 계층이 한다(레이트 리밋이 먼저다).")


class JoinResponse(BaseModel):
    trip_id: str
    participant: ParticipantOut
    participant_token: str


# ── spot ──────────────────────────────────────────────────────────────────
class SpotCreateRequest(Request):
    time_label: str = Field(default="오전", max_length=16)
    name: str = Field(min_length=1, max_length=120)
    name_original: str = Field(default="", max_length=120)
    tip: str = Field(default="", max_length=500)
    hours_text: str = Field(default="", max_length=200)
    closed_text: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=1000)
    recommendation: str = Field(default="", max_length=500)
    lat: Latitude
    lng: Longitude
    dwell_minutes: DwellMinutes | None = None


class SpotUpdateRequest(Request):
    """`version` 외 모든 필드는 선택이다. **준 필드만** 갱신한다(`exclude_unset`).

    `version` 은 **리소스 낙관적 잠금**이다. 여행 동기화용 `revision` 과 다른 것이다(§12 F3).
    """

    version: int = Field(ge=1)
    time_label: str | None = Field(default=None, max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    name_original: str | None = Field(default=None, max_length=120)
    tip: str | None = Field(default=None, max_length=500)
    hours_text: str | None = Field(default=None, max_length=200)
    closed_text: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    recommendation: str | None = Field(default=None, max_length=500)
    lat: Latitude | None = None
    lng: Longitude | None = None
    dwell_minutes: DwellMinutes | None = None

    def changes(self) -> dict[str, Any]:
        """`version` 을 뺀, **실제로 보낸** 필드만. `null` 을 보낸 것과 안 보낸 것을 구분한다."""
        given = self.model_dump(exclude_unset=True)
        given.pop("version", None)
        nullable = {"dwell_minutes"}
        return {key: value for key, value in given.items() if value is not None or key in nullable}


class HoursSpecOut(BaseModel):
    status: Literal["open_range", "open_24h", "weekday_range", "unknown"]
    approximate: bool
    open_local: str | None
    close_local: str | None
    crosses_midnight: bool
    pattern: str
    weekday_open: dict[str, list[str]] | None = None


class DoneStateOut(BaseModel):
    is_done: bool
    by_participant_id: str | None
    by_display_name: str | None
    at: str | None


class SpotOut(BaseModel):
    id: str
    day_index: int
    position: int
    time_label: str
    fixed_start_local: str | None
    name: str
    name_original: str
    tip: str
    hours_text: str
    closed_text: str
    description: str
    recommendation: str
    lat: float
    lng: float
    dwell_minutes: int
    dwell_source: Literal["explicit", "time_band"]
    version: int
    done: DoneStateOut
    hours: HoursSpecOut
    closed_weekdays: list[int]
    directions_url: str
    updated_at: str


class ScheduleOut(BaseModel):
    eta_local: str
    depart_local: str
    eta_day_offset: int
    depart_day_offset: int


class LegOut(BaseModel):
    """`travel_seconds` 와 `overhead_seconds` 를 따로 노출한다 — 비례성은 주행 성분에
    대해서만 성립한다(§12 F8 · AC-020)."""

    to_spot_id: str
    distance_m: float
    mode: Literal["walk", "transit"]
    travel_seconds: int
    overhead_seconds: int
    minutes: int
    estimated: bool


class SpotInDayOut(SpotOut):
    schedule: ScheduleOut | None
    leg_to_next: LegOut | None


class ReorderRequest(Request):
    expected_revision: int = Field(ge=1)
    spot_ids: list[str] = Field(description="그 일자의 스팟 전체 집합과 정확히 일치해야 한다 (누락·중복·외부 ID → 422)")


class DayOrderResponse(BaseModel):
    day_index: int
    spot_ids: list[str]
    revision: int


class MoveRequest(Request):
    expected_revision: int = Field(ge=1)
    to_day_index: int = Field(ge=1, le=4)
    to_position: int | None = Field(default=None, ge=0)


class MoveResponse(BaseModel):
    spot_id: str
    from_day_index: int
    to_day_index: int
    revision: int


class DoneRequest(Request):
    done: bool


class DoneResponse(BaseModel):
    spot_id: str
    done: DoneStateOut
    progress: ProgressOut
    revision: int


class RouteProposalOut(BaseModel):
    day_index: int
    current_order: list[str]
    proposed_order: list[str]
    current_total_distance_m: float
    proposed_total_distance_m: float
    improved: bool
    algorithm: Literal["exact", "two_opt"]
    anchored_spot_ids: list[str]


# ── state ─────────────────────────────────────────────────────────────────
class WarningOut(BaseModel):
    spot_id: str
    kind: Literal["closed_on_arrival", "closed_day"]
    message: str
    eta_local: str | None = None
    open_local: str | None = None
    close_local: str | None = None


class ConflictOut(BaseModel):
    spot_id: str
    fixed_spot_id: str
    overlap_start_local: str
    overlap_end_local: str
    overlap_minutes: int


class DayTotalsOut(BaseModel):
    distance_m: float
    travel_minutes: int
    dwell_minutes: int


class DayStateOut(BaseModel):
    day_index: int
    date: str
    weekday: int
    title: str
    area: str
    color: str
    start_local: str
    spots: list[SpotInDayOut]
    totals: DayTotalsOut


class ExpensesSummaryOut(BaseModel):
    count: int
    total_minor: int
    currency: Literal["HKD"]


class TripStateOut(BaseModel):
    trip: TripOut
    participants: list[ParticipantOut]
    progress: ProgressOut
    days: list[DayStateOut]
    warnings: list[WarningOut]
    conflicts: list[ConflictOut]
    expenses_summary: ExpensesSummaryOut


# ── expense ───────────────────────────────────────────────────────────────
class ExpenseCreateRequest(Request):
    payer_id: str
    amount_minor: int = Field(ge=1, description="HKD cent. 0 이하면 422 (AC-012)")
    currency: Literal["HKD"] = "HKD"
    note: str = Field(default="", max_length=200)
    spot_id: str | None = None
    spent_at: str | None = None
    share_participant_ids: list[str] = Field(min_length=1, description="비어 있으면 422 (AC-012)")


class ExpenseUpdateRequest(Request):
    version: int = Field(ge=1)
    payer_id: str | None = None
    amount_minor: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=200)
    spot_id: str | None = None
    share_participant_ids: list[str] | None = Field(default=None, min_length=1)

    def changes(self) -> dict[str, Any]:
        given = self.model_dump(exclude_unset=True)
        given.pop("version", None)
        given.pop("share_participant_ids", None)
        nullable = {"spot_id"}
        return {key: value for key, value in given.items() if value is not None or key in nullable}


class ExpenseShareOut(BaseModel):
    participant_id: str
    share_minor: int


class ExpenseOut(BaseModel):
    id: str
    payer_id: str
    amount_minor: int
    amount_krw: int | None
    currency: Literal["HKD"]
    note: str
    spot_id: str | None
    spent_at: str
    version: int
    shares: list[ExpenseShareOut]


class ExternalMetaOut(BaseModel):
    available: bool
    stale: bool
    fetched_at: str | None


class FxResponseOut(ExternalMetaOut):
    base: Literal["HKD"] = "HKD"
    quote: Literal["KRW"] = "KRW"
    rate_micro: int | None = None
    rate_date: str | None = None
    source: Literal["frankfurter"] = "frankfurter"


class WeatherResponseOut(ExternalMetaOut):
    temp_c: float | None = None
    humidity_pct: int | None = None
    precipitation_mm: float | None = None
    weather_code: int | None = None
    today_max_c: float | None = None
    today_min_c: float | None = None
    precip_prob_pct: int | None = None
    observed_local: str | None = None
    source: Literal["open-meteo"] = "open-meteo"


class ExpenseListOut(BaseModel):
    items: list[ExpenseOut]
    total_minor: int
    total_krw: int | None
    currency: Literal["HKD"]
    fx: FxResponseOut


class BalanceOut(BaseModel):
    participant_id: str
    display_name: str
    paid_minor: int
    owed_minor: int
    balance_minor: int


class TransferOut(BaseModel):
    from_participant_id: str
    to_participant_id: str
    amount_minor: int


class SettlementOut(BaseModel):
    currency: Literal["HKD"]
    balances: list[BalanceOut]
    transfers: list[TransferOut]
