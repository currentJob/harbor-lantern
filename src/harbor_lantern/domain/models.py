"""도메인 타입 (설계서 §6.2 · DSN-02).

전부 `frozen=True` dataclass 다 — 계산 결과가 나중에 다른 계층에서 조용히 바뀌는 일을
막는다. 시각은 전부 **자정 기준 분(minute-of-day)** 정수이고, 금액은 전부 **정수 minor
unit**(HKD cent)이다. `HH:MM` 문자열로 시각을 들고 다니면 자정 넘김에서 반드시 깨진다 —
문자열 변환은 API 경계에서만 한다(§6.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "ClosedSpec",
    "Conflict",
    "HoursSpec",
    "LatLng",
    "Leg",
    "RouteProposal",
    "ScheduledSpot",
    "SpotInput",
    "Transfer",
    "Warning",
]

TravelMode = Literal["walk", "transit"]
HoursStatus = Literal["open_range", "open_24h", "unknown"]
DwellSource = Literal["explicit", "time_band"]
OpenState = Literal["open", "closed", "unknown"]
RouteAlgorithm = Literal["exact", "two_opt"]


@dataclass(frozen=True)
class LatLng:
    """위·경도. 값은 시드 원본 그대로다 — 반올림하지 않는다(§5.3 · AC-034)."""

    lat: float
    lng: float


@dataclass(frozen=True)
class Leg:
    """스팟 사이 한 구간의 이동 추정 (DSN-14 · §6.7).

    `travel_seconds`(주행)와 `overhead_seconds`(환승·대기)를 **따로** 노출한다.
    대중교통은 덧셈 항이 있어서 우회계수를 2배로 해도 `minutes` 가 2배가 되지 않는다 —
    비례성은 주행 성분에 대해서만 성립한다(설계서 §12 F8 · AC-020).
    """

    distance_m: float
    mode: TravelMode
    travel_seconds: int
    overhead_seconds: int
    minutes: int
    estimated: bool = True


@dataclass(frozen=True)
class HoursSpec:
    """영업시간 파싱 결과 (DSN-16 · §6.9).

    `open_min`·`close_min` 은 자정 기준 분이다. **못 읽으면 `status="unknown"` 이고,
    `unknown` 은 경고를 만들지 않는다** — 거짓 경고 하나가 진짜 경고 전부를 무시하게
    만든다(R1 · AC-024).
    """

    status: HoursStatus
    open_min: int | None = None
    close_min: int | None = None
    crosses_midnight: bool = False
    approximate: bool = False
    weekday_open: dict[int, tuple[int, int]] | None = None
    pattern: str = ""


@dataclass(frozen=True)
class ClosedSpec:
    """휴무 요일 집합. 0=월 … 6=일 (`datetime.date.weekday()` 와 같은 규약)."""

    weekdays: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SpotInput:
    """타임라인·최적화 계산의 입력 한 건 (§6.8 `build_timeline` 의 `SpotInput`).

    도메인은 DB 행을 모른다. 서비스 계층이 `spot` 행을 이 모양으로 좁혀서 넘긴다 —
    계산에 필요한 것만 담는다. `dwell_minutes` 가 `None` 이면 `time_label` 로
    기본 체류시간을 정한다(O6 · §6.8).
    """

    spot_id: str
    coord: LatLng
    time_label: str
    dwell_minutes: int | None = None
    fixed_start_local: str | None = None


@dataclass(frozen=True)
class ScheduledSpot:
    """타임라인 한 칸 (DSN-15 · §6.8).

    `eta_min`·`depart_min` 은 **일자 시작 자정으로부터의 분**이다. 1440 을 넘을 수 있다.
    """

    spot_id: str
    eta_min: int
    depart_min: int
    dwell_minutes: int
    dwell_source: DwellSource


@dataclass(frozen=True)
class Warning:  # noqa: A001 — 설계서 §6.2 가 정한 이름이다. 내장 `Warning` 을 가린다.
    """영업시간·휴무 경고 (DSN-17 · §6.10). 스팟당 종류(`kind`)별 최대 1건."""

    spot_id: str
    kind: str
    message: str
    eta_local: str | None = None


@dataclass(frozen=True)
class Conflict:
    """고정시각 일정과의 충돌 (DSN-18 · §6.12).

    앞 스팟의 `depart_min` 이 고정시각을 **초과**할 때만 1건이다(`>`, `>=` 아님) —
    딱 맞는 일정을 충돌로 잡으면 사용자가 경고를 끄고 진짜를 놓친다(AC-025).
    """

    spot_id: str
    fixed_spot_id: str
    overlap_start_local: str
    overlap_end_local: str
    overlap_minutes: int


@dataclass(frozen=True)
class Transfer:
    """정산 송금 1건 (DSN-13 · §6.6). 금액은 정수 minor unit."""

    from_participant_id: str
    to_participant_id: str
    amount_minor: int


@dataclass(frozen=True)
class RouteProposal:
    """동선 최적화 제안 (DSN-19 · §6.16).

    **읽기 전용 제안이다** — 적용은 기존 재정렬 엔드포인트가 한다.
    `proposed_total_distance_m <= current_total_distance_m` 이 아니면 `improved=False`
    이고 `proposed_order` 는 현재 순서 그대로다(AC-017 — 부등식이 깨지지 않는다).
    """

    current_order: tuple[str, ...]
    proposed_order: tuple[str, ...]
    current_total_distance_m: float
    proposed_total_distance_m: float
    improved: bool
    algorithm: RouteAlgorithm
    anchored_spot_ids: tuple[str, ...] = ()
