"""경고 생성기 — DSN-17 (설계서 §6.10 · REQ-012 · AC-023 · AC-024).

경고는 스팟당 **종류별 최대 1건**이고, 영업시간이 `unknown` 이면 **0건**이다.
`unknown` 을 "아마 닫혔을 것"으로 취급하는 순간 시드 27건 중 6건이 매번 경고를
쏟아내고, 그 다음부터 아무도 경고를 안 본다(R1 · §12 F9).

`visit_weekday` 는 그 일자의 `date`(HKT 달력일)에서 계산해 **인자로 받는다**.
현재 시각을 읽지 않는다 — `/state` 가 서버 시계에 의존하면 ETag 가 거짓말을 한다(§12 F1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from harbor_lantern.domain.hours import is_open_at
from harbor_lantern.domain.models import ClosedSpec, HoursSpec, ScheduledSpot, Warning
from harbor_lantern.domain.util import MINUTES_PER_DAY, format_hhmm

__all__ = ["KIND_CLOSED_DAY", "KIND_CLOSED_ON_ARRIVAL", "WEEKDAY_NAMES", "build_warnings"]

KIND_CLOSED_ON_ARRIVAL = "closed_on_arrival"
KIND_CLOSED_DAY = "closed_day"

WEEKDAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")


def build_warnings(
    scheduled: Sequence[ScheduledSpot],
    hours: Mapping[str, HoursSpec],
    closed: Mapping[str, ClosedSpec],
    visit_weekday: int,
) -> list[Warning]:
    """도착 예상시각 기준 닫힘 경고 + 휴무 요일 경고.

    | 조건 | `kind` |
    |------|--------|
    | 영업시간 `unknown`(또는 표에 없음) | **경고 없음** — 응답의 `hours.status` 로만 알린다 |
    | 개방 구간 밖 도착 | `closed_on_arrival` |
    | `open_24h` | 없음 |
    | 방문 요일이 휴무 요일 | `closed_day` |

    반환 순서는 입력 스팟 순서를 따르고, 한 스팟 안에서는 닫힘 → 휴무 순이다.
    """
    warnings: list[Warning] = []

    for item in scheduled:
        eta_local = format_hhmm(item.eta_min)
        spec = hours.get(item.spot_id)
        if spec is not None:
            state = is_open_at(spec, item.eta_min % MINUTES_PER_DAY, visit_weekday)
            if state == "closed":
                warnings.append(
                    Warning(
                        spot_id=item.spot_id,
                        kind=KIND_CLOSED_ON_ARRIVAL,
                        message=_closed_message(spec, visit_weekday, eta_local),
                        eta_local=eta_local,
                    )
                )

        closed_spec = closed.get(item.spot_id)
        if closed_spec is not None and visit_weekday in closed_spec.weekdays:
            warnings.append(
                Warning(
                    spot_id=item.spot_id,
                    kind=KIND_CLOSED_DAY,
                    message=f"{_weekday_name(visit_weekday)}요일 휴무입니다.",
                    eta_local=eta_local,
                )
            )

    return warnings


def _weekday_name(weekday: int) -> str:
    return WEEKDAY_NAMES[weekday % 7]


def _closed_message(spec: HoursSpec, weekday: int, eta_local: str) -> str:
    """"몇 시에 도착하는데 몇 시부터 몇 시까지 연다"를 한 줄로. 근사치면 그렇다고 적는다."""
    window = spec.weekday_open.get(weekday) if spec.weekday_open is not None else None
    if window is None and spec.open_min is not None and spec.close_min is not None:
        window = (spec.open_min, spec.close_min)
    if window is None:
        return f"도착 예상 {eta_local} — 영업시간 밖입니다."
    prefix = "대략 " if spec.approximate else ""
    return f"도착 예상 {eta_local} — 영업시간 {prefix}{_window_hhmm(window[0])}~{_window_hhmm(window[1])} 밖입니다."


def _window_hhmm(minutes: int) -> str:
    """개방 구간 표기 전용. `1440` 은 `'24:00'` 으로 적는다.

    `util.format_hhmm` 은 하루를 감아서 `'00:00'` 을 낸다(시각 표기용이라 옳다).
    그런데 `"매일 대략 18:00-24:00"` 의 마감을 `00:00` 으로 보여 주면 사람이
    "자정에 여는 곳인가" 하고 두 번 읽는다.
    """
    return "24:00" if minutes == MINUTES_PER_DAY else format_hhmm(minutes)
