"""동선 최적화 — AC-017 · AC-018 (설계서 §6.16).

두 가지가 절대 깨지면 안 된다.

1. **제안 총거리 ≤ 현재 총거리** (AC-017). 구현이 "더 나쁜 순서"를 제안하는 순간
   사용자는 두 번 다시 이 버튼을 누르지 않는다.
2. **앵커(고정시각 스팟)는 자리를 지킨다** (AC-018). 20:00 조명쇼가 오후로 옮겨 가면
   그건 최적화가 아니라 파괴다.
"""

from __future__ import annotations

import random

import pytest

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.route import EXACT_MAX_FREE, optimize_day

# 일직선 위의 점들 — 최적 순서를 사람이 손으로 알 수 있다.
LINE = {
    "p0": LatLng(22.30, 114.10),
    "p1": LatLng(22.30, 114.11),
    "p2": LatLng(22.30, 114.12),
    "p3": LatLng(22.30, 114.13),
    "p4": LatLng(22.30, 114.14),
}


def total(order: list[str] | tuple[str, ...], coords: dict[str, LatLng]) -> float:
    return sum(haversine_m(coords[order[i]], coords[order[i + 1]]) for i in range(len(order) - 1))


class TestAc017:
    """제안은 현재보다 짧거나(개선) 현재 그대로(개선 없음)다."""

    def test_ac017_proposal_is_never_longer(self) -> None:
        proposal = optimize_day(["p0", "p3", "p1", "p4", "p2"], LINE, set())
        assert proposal.proposed_total_distance_m <= proposal.current_total_distance_m

    def test_ac017_shuffled_line_is_straightened(self) -> None:
        proposal = optimize_day(["p0", "p3", "p1", "p4", "p2"], LINE, set())
        assert proposal.improved is True
        assert proposal.proposed_order in (("p0", "p1", "p2", "p3", "p4"), ("p4", "p3", "p2", "p1", "p0"))
        assert proposal.proposed_total_distance_m == pytest.approx(total(proposal.proposed_order, LINE))

    def test_ac017_already_optimal_order_is_left_alone(self) -> None:
        """개선이 없으면 순서를 바꾸지 않는다 — `improved=False` 이고 제안 = 현재."""
        proposal = optimize_day(["p0", "p1", "p2", "p3", "p4"], LINE, set())
        assert proposal.improved is False
        assert proposal.proposed_order == proposal.current_order
        assert proposal.proposed_total_distance_m == proposal.current_total_distance_m

    def test_ac017_spot_set_is_preserved(self) -> None:
        order = ["p0", "p3", "p1", "p4", "p2"]
        proposal = optimize_day(order, LINE, set())
        assert sorted(proposal.proposed_order) == sorted(order)
        assert len(set(proposal.proposed_order)) == len(order)

    @pytest.mark.parametrize("seed", range(12))
    def test_ac017_holds_for_random_orders(self, seed: int) -> None:
        rng = random.Random(seed)  # noqa: S311 — 테스트 입력 생성용. 암호 용도가 아니다
        coords = {f"s{i}": LatLng(22.2 + rng.random() * 0.2, 114.1 + rng.random() * 0.2) for i in range(6)}
        order = list(coords)
        rng.shuffle(order)
        proposal = optimize_day(order, coords, set())
        assert proposal.proposed_total_distance_m <= proposal.current_total_distance_m + 1e-9
        assert sorted(proposal.proposed_order) == sorted(order)


class TestAc018Anchors:
    """고정시각 스팟은 최적화 후에도 시각 순서상의 자리를 지킨다."""

    def test_ac018_anchor_keeps_its_index(self) -> None:
        order = ["p0", "p3", "p1", "p4", "p2"]
        proposal = optimize_day(order, LINE, {"p3"})
        assert proposal.proposed_order[1] == "p3"  # 원래 인덱스 1 그대로
        assert proposal.anchored_spot_ids == ("p3",)

    def test_ac018_every_anchor_keeps_its_index(self) -> None:
        order = ["p0", "p3", "p1", "p4", "p2"]
        proposal = optimize_day(order, LINE, {"p0", "p4"})
        assert proposal.proposed_order[0] == "p0"
        assert proposal.proposed_order[3] == "p4"

    def test_ac018_all_anchored_means_nothing_moves(self) -> None:
        order = ["p2", "p0", "p1"]
        proposal = optimize_day(order, LINE, set(order))
        assert proposal.proposed_order == tuple(order)
        assert proposal.improved is False

    def test_ac018_anchor_can_prevent_the_global_optimum(self) -> None:
        """앵커 때문에 더 짧은 순서를 못 고르는 것은 **정상**이다 — 시각이 거리보다 세다."""
        order = ["p4", "p0", "p1", "p2", "p3"]
        free = optimize_day(order, LINE, set())
        anchored = optimize_day(order, LINE, {"p4"})
        assert anchored.proposed_order[0] == "p4"
        assert free.proposed_total_distance_m <= anchored.proposed_total_distance_m

    def test_ac018_deterministic_across_runs(self) -> None:
        order = ["p0", "p3", "p1", "p4", "p2"]
        first = optimize_day(order, LINE, {"p3"})
        for _ in range(5):
            assert optimize_day(order, LINE, {"p3"}) == first


class TestAlgorithmSelection:
    def test_small_days_use_exact_search(self) -> None:
        proposal = optimize_day(list(LINE), LINE, set())
        assert proposal.algorithm == "exact"

    def test_many_free_spots_switch_to_two_opt(self) -> None:
        """자유 스팟 8개 이상이면 완전 탐색(8! = 40,320)을 포기한다 (§6.16 · §8 예산)."""
        coords = {f"s{i}": LatLng(22.3 + i * 0.001, 114.1 + (i % 3) * 0.004) for i in range(9)}
        proposal = optimize_day(list(coords), coords, set())
        assert len(coords) > EXACT_MAX_FREE
        assert proposal.algorithm == "two_opt"
        assert proposal.proposed_total_distance_m <= proposal.current_total_distance_m + 1e-9

    def test_two_opt_is_deterministic(self) -> None:
        coords = {f"s{i}": LatLng(22.3 + (i % 4) * 0.01, 114.1 + (i % 5) * 0.01) for i in range(10)}
        order = list(coords)
        assert optimize_day(order, coords, set()) == optimize_day(order, coords, set())

    def test_anchors_reduce_the_free_count_for_algorithm_choice(self) -> None:
        coords = {f"s{i}": LatLng(22.3 + i * 0.002, 114.1 + (i % 3) * 0.003) for i in range(9)}
        proposal = optimize_day(list(coords), coords, {"s0", "s8"})
        assert proposal.algorithm == "exact"  # 자유 7개

    def test_two_opt_also_respects_anchors(self) -> None:
        coords = {f"s{i}": LatLng(22.3 + (i % 4) * 0.01, 114.1 + (i % 5) * 0.01) for i in range(10)}
        order = list(coords)
        proposal = optimize_day(order, coords, {"s3"})
        assert proposal.algorithm == "two_opt"
        assert proposal.proposed_order[3] == "s3"


class TestEdges:
    def test_empty_day(self) -> None:
        proposal = optimize_day([], {}, set())
        assert proposal.proposed_order == ()
        assert proposal.current_total_distance_m == 0.0
        assert proposal.improved is False

    def test_single_spot(self) -> None:
        proposal = optimize_day(["p0"], LINE, set())
        assert proposal.proposed_order == ("p0",)
        assert proposal.improved is False

    def test_two_spots_cannot_be_improved(self) -> None:
        proposal = optimize_day(["p0", "p4"], LINE, set())
        assert proposal.improved is False
        assert proposal.proposed_order == ("p0", "p4")

    def test_missing_coordinates_fail_loudly(self) -> None:
        with pytest.raises(ValueError, match="좌표가 없는"):
            optimize_day(["p0", "ghost"], LINE, set())

    def test_duplicate_spot_ids_fail_loudly(self) -> None:
        with pytest.raises(ValueError, match="중복"):
            optimize_day(["p0", "p0"], LINE, set())
