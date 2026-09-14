"""동선 최적화 — DSN-19 (설계서 §6.16 · REQ-009 · AC-017 · AC-018).

**제안만 한다. 쓰기는 하지 않는다.** 적용은 기존 재정렬 경로(`PUT /days/{n}/order`)가
맡는다 — 쓰기 경로를 늘리지 않는 것이 §6.11 의 원자성 설계를 한 곳에 유지하는 방법이다.

앵커(= `fixed_start_local` 이 있는 스팟)는 **현재 인덱스 자리를 그대로 유지**하고
자유 스팟만 남은 자리에 재배치한다. 이것으로 "시각 순서상의 자리 유지"(AC-018)가
탐색 알고리즘과 무관하게 구조적으로 보장된다 — 나중에 탐색을 바꿔도 깨지지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet  # 설계서 §6.16 의 이름을 그대로 쓴다
from itertools import permutations

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng, RouteAlgorithm, RouteProposal

__all__ = ["EXACT_MAX_FREE", "optimize_day"]

# 자유 스팟 f 가 이 수 이하이면 완전 탐색(최대 7! = 5040가지).
# 일자 최대 스팟 수가 9(Day 2)라 9! = 362,880 은 §8 의 응답시간 예산을 넘는다.
EXACT_MAX_FREE = 7

# 부동소수 거리 비교 허용오차(미터). 6371km 규모의 합에서 배정밀도 오차는
# 1e-9 m 근처다 — 이보다 작은 '개선'은 개선이 아니라 잡음이다.
_EPSILON_M = 1e-9


def optimize_day(
    spot_ids: Sequence[str],
    coords: Mapping[str, LatLng],
    anchored: AbstractSet[str],
) -> RouteProposal:
    """일자 하나의 방문 순서 재배열 제안.

    - 목적함수: 연속 쌍의 하버사인 거리 합(귀환 없음). 거리 행렬은 요청당 1회 계산.
    - 자유 스팟 f ≤ 7 → 완전 탐색(`algorithm="exact"`), f ≥ 8 → 결정론 2-opt(`"two_opt"`).
    - **AC-017 보장**: 제안 총거리가 현재보다 **작을 때만** 새 순서를 담는다. 같거나 크면
      `improved=False` 이고 `proposed_order` 는 현재 순서 그대로다. 부등식이 깨질 수 없다.
    - 동률 타이브레이크는 스팟 ID 시퀀스의 사전순 최소 — 같은 입력에 같은 출력(AC-018).
    """
    order = tuple(spot_ids)
    missing = [spot_id for spot_id in order if spot_id not in coords]
    if missing:
        raise ValueError(f"좌표가 없는 스팟이 있다: {missing}")
    if len(set(order)) != len(order):
        raise ValueError("스팟 id 가 중복됐다 — 일자 내 순서는 집합이어야 한다")

    free_positions = [index for index, spot_id in enumerate(order) if spot_id not in anchored]
    free_items = [order[index] for index in free_positions]
    algorithm: RouteAlgorithm = "exact" if len(free_items) <= EXACT_MAX_FREE else "two_opt"

    # 거리 행렬은 요청당 1회 (§6.16). 완전 탐색은 5040가지 순열을 훑으므로
    # 하버사인을 순열마다 다시 부르면 같은 값을 수만 번 계산한다.
    matrix = _distance_matrix(order, coords)
    current_total = _total_distance(order, matrix)
    anchored_ids = tuple(spot_id for spot_id in order if spot_id in anchored)

    if len(free_items) < 2:
        # 바꿀 수 있는 것이 없다. 탐색을 돌려도 결과는 현재 순서다.
        return RouteProposal(
            current_order=order,
            proposed_order=order,
            current_total_distance_m=current_total,
            proposed_total_distance_m=current_total,
            improved=False,
            algorithm=algorithm,
            anchored_spot_ids=anchored_ids,
        )

    if algorithm == "exact":
        best_order, best_total = _search_exact(order, free_positions, free_items, matrix)
    else:
        best_order, best_total = _search_two_opt(order, free_positions, free_items, matrix)

    improved = best_total < current_total - _EPSILON_M
    return RouteProposal(
        current_order=order,
        proposed_order=best_order if improved else order,
        current_total_distance_m=current_total,
        proposed_total_distance_m=best_total if improved else current_total,
        improved=improved,
        algorithm=algorithm,
        anchored_spot_ids=anchored_ids,
    )


def _distance_matrix(order: Sequence[str], coords: Mapping[str, LatLng]) -> dict[tuple[str, str], float]:
    """모든 쌍의 하버사인 거리. 대칭이므로 한 번 재서 양쪽에 넣는다."""
    matrix: dict[tuple[str, str], float] = {}
    for i, left in enumerate(order):
        matrix[(left, left)] = 0.0
        for right in order[i + 1 :]:
            distance = haversine_m(coords[left], coords[right])
            matrix[(left, right)] = distance
            matrix[(right, left)] = distance
    return matrix


def _total_distance(order: Sequence[str], matrix: Mapping[tuple[str, str], float]) -> float:
    return sum(matrix[(order[i], order[i + 1])] for i in range(len(order) - 1))


def _rebuild(order: Sequence[str], free_positions: Sequence[int], arrangement: Sequence[str]) -> tuple[str, ...]:
    """자유 자리에 `arrangement` 를 끼운 전체 순서. 앵커는 원래 인덱스 그대로 남는다."""
    result = list(order)
    for position, spot_id in zip(free_positions, arrangement, strict=True):
        result[position] = spot_id
    return tuple(result)


def _better(
    candidate_total: float,
    candidate_order: tuple[str, ...],
    best_total: float,
    best_order: tuple[str, ...],
) -> bool:
    """더 짧으면 채택. 같으면 **ID 시퀀스 사전순**으로 자른다 (AC-018 결정론)."""
    if candidate_total < best_total - _EPSILON_M:
        return True
    return abs(candidate_total - best_total) <= _EPSILON_M and candidate_order < best_order


def _search_exact(
    order: tuple[str, ...],
    free_positions: Sequence[int],
    free_items: Sequence[str],
    matrix: Mapping[tuple[str, str], float],
) -> tuple[tuple[str, ...], float]:
    """완전 탐색. 순열 열거를 **정렬된 목록**에서 시작해 열거 순서까지 고정한다."""
    best_order = tuple(order)
    best_total = _total_distance(best_order, matrix)
    for arrangement in permutations(sorted(free_items)):
        candidate = _rebuild(order, free_positions, arrangement)
        candidate_total = _total_distance(candidate, matrix)
        if _better(candidate_total, candidate, best_total, best_order):
            best_order, best_total = candidate, candidate_total
    return best_order, best_total


def _search_two_opt(
    order: tuple[str, ...],
    free_positions: Sequence[int],
    free_items: Sequence[str],
    matrix: Mapping[tuple[str, str], float],
) -> tuple[tuple[str, ...], float]:
    """결정론 2-opt — 현재 순서에서 출발, 개선이 없을 때까지 **최선-개선** 반복.

    뒤집는 대상은 전체 순서가 아니라 **자유 스팟 목록**이다. 전체 순서를 뒤집으면
    앵커가 딸려 움직여 AC-018 이 깨진다.

    인덱스 쌍은 오름차순으로 고정 순회하고, 동률이면 먼저 나온 것을 쓴다.
    반복 상한을 두는 이유: 부동소수 비교가 두 순서를 서로 '더 낫다'고 판정하면
    영원히 돈다. `_EPSILON_M` 이 그걸 막지만, 막지 못했을 때 서버가 멈추는 것보다
    덜 좋은 답을 주는 편이 낫다.
    """
    current = list(free_items)
    best_order = _rebuild(order, free_positions, current)
    best_total = _total_distance(best_order, matrix)

    max_rounds = len(current) * len(current)
    for _ in range(max_rounds):
        round_order, round_total = best_order, best_total
        round_items: list[str] | None = None
        for i in range(len(current) - 1):
            for j in range(i + 1, len(current)):
                candidate_items = current[:i] + current[i : j + 1][::-1] + current[j + 1 :]
                candidate = _rebuild(order, free_positions, candidate_items)
                candidate_total = _total_distance(candidate, matrix)
                if _better(candidate_total, candidate, round_total, round_order):
                    round_order, round_total, round_items = candidate, candidate_total, candidate_items
        if round_items is None:
            break
        current, best_order, best_total = round_items, round_order, round_total

    return best_order, best_total
