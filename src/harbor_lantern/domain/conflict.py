"""고정시각 충돌 검사 — DSN-18 (설계서 §6.12 · REQ-013 · AC-025).

고정시각 스팟 F(예: 20:00 심포니 오브 라이트) **바로 앞 스팟 P** 의 출발시각이
F 의 시작시각을 **초과하면**(`>`) 충돌 1건이다.

**비교는 반드시 `>` 다.** `>=` 로 쓰면 20:00 정각에 딱 맞춰 도착하는 일정이 충돌로
잡힌다 — 사용자는 그 경고를 끄고, 그 다음 진짜 충돌을 놓친다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from harbor_lantern.domain.models import Conflict, ScheduledSpot
from harbor_lantern.domain.util import format_hhmm

__all__ = ["find_conflicts"]


def find_conflicts(scheduled: Sequence[ScheduledSpot], fixed: Mapping[str, int]) -> list[Conflict]:
    """고정시각 침범 목록. `fixed` 는 `spot_id → 시작시각(자정 기준 분)`.

    AC-025 검산: P 가 19:50(1190) 도착 + 40분 체류 → depart 20:30(1230).
    F = 20:00(1200). `1230 > 1200` → 충돌 1건, 구간 `20:00~20:30`, 30분.
    체류를 10분으로 줄이면 depart = 1200 이고 `1200 > 1200` 이 거짓 → 0건.

    일자에서 F 가 첫 스팟이면 앞이 없으므로 충돌도 없다. `fixed` 에 있지만 일정에
    없는 스팟 id 는 무시한다 — 다른 일자의 고정 스팟이 섞여 들어와도 조용히 넘긴다.
    """
    conflicts: list[Conflict] = []

    for index, item in enumerate(scheduled):
        start_min = fixed.get(item.spot_id)
        if start_min is None or index == 0:
            continue
        previous = scheduled[index - 1]
        if previous.depart_min > start_min:
            conflicts.append(
                Conflict(
                    spot_id=previous.spot_id,
                    fixed_spot_id=item.spot_id,
                    overlap_start_local=format_hhmm(start_min),
                    overlap_end_local=format_hhmm(previous.depart_min),
                    overlap_minutes=previous.depart_min - start_min,
                )
            )

    return conflicts
