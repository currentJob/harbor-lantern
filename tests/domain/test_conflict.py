"""고정시각 충돌 검사 — AC-025 (설계서 §6.12).

AC-025 의 수치를 그대로 쓴다: 20:00 시작 고정 일정 앞 스팟이 19:50 도착 · 40분 체류
→ 충돌 1건 / 체류를 줄이면 0건. **경계 비교가 `>` 인지 `>=` 인지가 이 파일의 핵심**이다.
"""

from __future__ import annotations

from harbor_lantern.domain.conflict import find_conflicts
from harbor_lantern.domain.models import ScheduledSpot

SYMPHONY_START = 20 * 60  # 1200


def scheduled(spot_id: str, eta_min: int, dwell: int) -> ScheduledSpot:
    return ScheduledSpot(
        spot_id=spot_id,
        eta_min=eta_min,
        depart_min=eta_min + dwell,
        dwell_minutes=dwell,
        dwell_source="explicit",
    )


class TestAc025:
    def test_ac025_overlapping_previous_spot_is_one_conflict(self) -> None:
        """19:50 도착 + 40분 체류 → 20:30 출발. 20:00 시작을 30분 침범한다."""
        schedule = [scheduled("harbour", 19 * 60 + 50, 40), scheduled("symphony", 20 * 60 + 30, 13)]
        conflicts = find_conflicts(schedule, {"symphony": SYMPHONY_START})

        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict.spot_id == "harbour"
        assert conflict.fixed_spot_id == "symphony"
        assert conflict.overlap_start_local == "20:00"
        assert conflict.overlap_end_local == "20:30"
        assert conflict.overlap_minutes == 30

    def test_ac025_shorter_dwell_removes_the_conflict(self) -> None:
        """체류 10분 → 20:00 정각 출발. `1200 > 1200` 은 거짓이다."""
        schedule = [scheduled("harbour", 19 * 60 + 50, 10), scheduled("symphony", 20 * 60, 13)]
        assert find_conflicts(schedule, {"symphony": SYMPHONY_START}) == []

    def test_exactly_one_minute_over_is_a_conflict(self) -> None:
        schedule = [scheduled("harbour", 19 * 60 + 50, 11), scheduled("symphony", 20 * 60 + 1, 13)]
        conflicts = find_conflicts(schedule, {"symphony": SYMPHONY_START})
        assert [c.overlap_minutes for c in conflicts] == [1]

    def test_finishing_early_is_not_a_conflict(self) -> None:
        schedule = [scheduled("harbour", 18 * 60, 60), scheduled("symphony", 19 * 60 + 20, 13)]
        assert find_conflicts(schedule, {"symphony": SYMPHONY_START}) == []


class TestEdges:
    def test_fixed_spot_first_in_the_day_has_no_predecessor(self) -> None:
        schedule = [scheduled("symphony", 20 * 60, 13), scheduled("market", 20 * 60 + 20, 60)]
        assert find_conflicts(schedule, {"symphony": SYMPHONY_START}) == []

    def test_only_the_immediately_preceding_spot_is_checked(self) -> None:
        """두 칸 앞 스팟은 이미 떠났다 — 그것까지 충돌로 세면 경고가 두 배가 된다(§6.12)."""
        schedule = [
            scheduled("far", 10 * 60, 800),  # 23:20 출발 — 순서상 앞이지만 바로 앞이 아니다
            scheduled("near", 19 * 60, 30),  # 19:30 출발 — 문제 없다
            scheduled("symphony", 20 * 60, 13),
        ]
        assert find_conflicts(schedule, {"symphony": SYMPHONY_START}) == []

    def test_two_fixed_spots_report_independently(self) -> None:
        schedule = [
            scheduled("a", 9 * 60, 120),  # 11:00 출발 → 10:00 고정 침범 (60분)
            scheduled("fixed1", 11 * 60, 30),
            scheduled("b", 12 * 60, 30),  # 12:30 출발 → 14:00 고정 안전
            scheduled("fixed2", 14 * 60, 30),
        ]
        conflicts = find_conflicts(schedule, {"fixed1": 10 * 60, "fixed2": 14 * 60})
        assert [(c.spot_id, c.fixed_spot_id, c.overlap_minutes) for c in conflicts] == [("a", "fixed1", 60)]

    def test_unknown_fixed_ids_are_ignored(self) -> None:
        """다른 일자의 고정 스팟이 섞여 들어와도 조용히 넘긴다."""
        schedule = [scheduled("a", 9 * 60, 60), scheduled("b", 10 * 60, 60)]
        assert find_conflicts(schedule, {"elsewhere": 10 * 60}) == []

    def test_no_fixed_spots_means_no_conflicts(self) -> None:
        schedule = [scheduled("a", 9 * 60, 600), scheduled("b", 19 * 60, 60)]
        assert find_conflicts(schedule, {}) == []

    def test_empty_schedule(self) -> None:
        assert find_conflicts([], {"symphony": SYMPHONY_START}) == []

    def test_ac048_deterministic(self) -> None:
        schedule = [scheduled("harbour", 19 * 60 + 50, 40), scheduled("symphony", 20 * 60 + 30, 13)]
        fixed = {"symphony": SYMPHONY_START}
        assert find_conflicts(schedule, fixed) == find_conflicts(schedule, fixed)
