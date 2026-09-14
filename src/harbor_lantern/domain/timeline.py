"""체류시간과 도착 예상시각 — DSN-15 (설계서 §6.8 · REQ-011 · O6 · AC-021 · AC-048).

시각은 전부 **일자 시작 자정으로부터의 분**이다. 1440 을 넘을 수 있고(자정 넘김),
`HH:MM` 변환은 API 경계에서만 한다(§6.2).

**현재 시각을 읽지 않는다.** 입력은 일자 시작시각·스팟·계수뿐이라 같은 입력에 언제나
같은 출력이 나온다(AC-048). `/state` 응답이 서버 시계에 의존하면 ETag 가 거짓말을
한다(§12 F1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from harbor_lantern.config import DEFAULT_TIME_BAND, FIXED_TIME_BAND, TIME_BANDS, TimeBand, TravelConfig
from harbor_lantern.domain.models import DwellSource, Leg, ScheduledSpot, SpotInput
from harbor_lantern.domain.travel import leg as build_leg
from harbor_lantern.domain.util import is_hhmm, parse_hhmm

__all__ = ["build_timeline", "resolve_dwell", "resolve_start_min"]


def _band_of(time_label: str, bands: Mapping[str, TimeBand]) -> TimeBand:
    """라벨 → 기본값. 표에 없으면 `HH:MM` 인지 보고, 그것도 아니면 기본값(09:00/60분).

    **추측하지 않는다** — 모르는 라벨을 "아마 오후겠지"로 해석하면 하루가 조용히 밀린다.
    """
    label = time_label.strip()
    band = bands.get(label)
    if band is not None:
        return band
    if is_hhmm(label):
        minute = parse_hhmm(label)
        # is_hhmm 이 통과했으면 parse_hhmm 은 None 이 아니다. 그래도 방어한다.
        return TimeBand(minute if minute is not None else FIXED_TIME_BAND.start_min, FIXED_TIME_BAND.dwell_minutes)
    return DEFAULT_TIME_BAND


def resolve_dwell(spot: SpotInput, bands: Mapping[str, TimeBand] = TIME_BANDS) -> tuple[int, DwellSource]:
    """스팟의 체류시간(분)과 그 출처 (설계서 §6.8 표 · O6).

    명시값(`spot.dwell_minutes`)이 있으면 그대로 쓰고 출처는 `"explicit"`,
    없으면 시간대 라벨의 기본값이고 출처는 `"time_band"` 다. 출처를 함께 돌려주는
    이유: 화면이 "기본값입니다"를 표시할 수 있어야 사용자가 고칠 마음이 생긴다.

    설계서 §6.8 의 시그니처는 `resolve_dwell(spot, cfg)` 이지만 체류시간에 필요한 것은
    `TravelConfig` 가 아니라 **시간대 표**다. 그래서 두 번째 인자를 `bands` 로 두고
    기본값을 `config.TIME_BANDS` 로 잡았다 — 호출부는 `resolve_dwell(spot)` 만 쓰면 된다.
    """
    if spot.dwell_minutes is not None:
        return spot.dwell_minutes, "explicit"
    return _band_of(spot.time_label, bands).dwell_minutes, "time_band"


def resolve_start_min(time_label: str, bands: Mapping[str, TimeBand] = TIME_BANDS) -> int:
    """시간대 라벨의 **기본 시작시각**(자정 기준 분) — 설계서 §6.8 표의 가운데 열.

    일자의 `start_local` 은 시드에 굳혀 저장한다(§5.3). 유도값을 매번 재계산하면
    재정렬로 첫 스팟이 바뀔 때 하루 전체가 밀린다 — 그건 버그다. 이 함수는 그
    **최초 1회 유도**와 라벨 표 검증에 쓴다.
    """
    return _band_of(time_label, bands).start_min


def build_timeline(
    day_start_min: int,
    spots: Sequence[SpotInput],
    cfg: TravelConfig,
) -> tuple[list[ScheduledSpot], list[Leg | None]]:
    """일자 하나의 도착 예상시각과 구간 목록 (AC-021).

    ```
    eta[0]     = day_start_min
    depart[i]  = eta[i] + dwell[i]
    eta[i+1]   = depart[i] + leg[i].minutes
    ```

    돌려주는 구간 목록은 스팟과 **길이가 같고 마지막 칸이 `None`** 이다 —
    `legs[i]` 가 "스팟 i 에서 다음 스팟까지"이므로 카드 i 에 그대로 붙는다(§6.15).

    **고정시각 스팟이라고 해서 ETA 를 그 시각으로 당기거나 밀지 않는다.** 앞 일정이
    늦어지면 그 사실이 ETA 에 그대로 남아야 `conflict.find_conflicts` 가 침범을
    검출한다(§6.12 · AC-025). 여기서 몰래 맞춰 주면 충돌이 영원히 0건이 된다.
    """
    scheduled: list[ScheduledSpot] = []
    legs: list[Leg | None] = []

    eta = day_start_min
    for index, spot in enumerate(spots):
        dwell, source = resolve_dwell(spot)
        depart = eta + dwell
        scheduled.append(
            ScheduledSpot(
                spot_id=spot.spot_id,
                eta_min=eta,
                depart_min=depart,
                dwell_minutes=dwell,
                dwell_source=source,
            )
        )
        if index + 1 < len(spots):
            current_leg = build_leg(spot.coord, spots[index + 1].coord, cfg)
            legs.append(current_leg)
            eta = depart + current_leg.minutes
        else:
            legs.append(None)

    return scheduled, legs
