"""정산 — AC-014 · AC-015 · AC-016 · AC-048 (설계서 §6.6 · NFR-014).

**동점 상황의 타이브레이크를 명시적으로 고정한다.** 그리디에서 결정론을 보장하는 것은
정렬 규칙 하나뿐인데, 그게 없으면 실패가 "가끔"만 재현된다 — 가장 나쁜 종류의 버그다.
"""

from __future__ import annotations

import pytest

from harbor_lantern.domain.models import Transfer
from harbor_lantern.domain.settle import balances, settle


class TestBalances:
    """AC-014 — A 3000(A·B·C 균등) · B 600(B·C 균등) → A +2000 · B -700 · C -1300."""

    def test_ac014_worked_example(self) -> None:
        paid = {"A": 3000, "B": 600}
        owed = {"A": 1000, "B": 1000 + 300, "C": 1000 + 300}
        result = balances(paid, owed)
        assert result == {"A": 2000, "B": -700, "C": -1300}
        assert sum(result.values()) == 0

    def test_ac014_participants_appear_even_with_zero_balance(self) -> None:
        assert balances({"A": 500}, {"A": 500, "B": 0}) == {"A": 0, "B": 0}

    def test_participants_who_only_paid_or_only_owe_are_included(self) -> None:
        assert balances({"A": 100}, {"B": 100}) == {"A": 100, "B": -100}


class TestSettle:
    """AC-015 — 송금 2건(`B→A 700`, `C→A 1300`), 건수는 n-1 이하."""

    def test_ac015_worked_example(self) -> None:
        transfers = settle({"A": 2000, "B": -700, "C": -1300})
        assert transfers == [
            Transfer(from_participant_id="B", to_participant_id="A", amount_minor=700),
            Transfer(from_participant_id="C", to_participant_id="A", amount_minor=1300),
        ]

    def test_ac015_transfer_count_never_exceeds_n_minus_one(self) -> None:
        cases = [
            {"A": 2000, "B": -700, "C": -1300},
            {"A": 100, "B": 200, "C": -150, "D": -150},
            {"A": 1, "B": -1},
            {"A": 500, "B": -100, "C": -100, "D": -100, "E": -200},
        ]
        for case in cases:
            transfers = settle(case)
            assert len(transfers) <= len(case) - 1, case

    def test_ac015_transfers_settle_every_balance_exactly(self) -> None:
        case = {"A": 100, "B": 200, "C": -150, "D": -150}
        residual = dict(case)
        for transfer in settle(case):
            residual[transfer.from_participant_id] += transfer.amount_minor
            residual[transfer.to_participant_id] -= transfer.amount_minor
        assert set(residual.values()) == {0}

    def test_ac016_all_zero_balances_settle_to_nothing(self) -> None:
        assert settle({"A": 0, "B": 0, "C": 0}) == []

    def test_ac016_empty_input(self) -> None:
        assert settle({}) == []

    def test_ac016_same_input_same_output(self) -> None:
        case = {"A": 2000, "B": -700, "C": -1300}
        assert settle(case) == settle(case) == settle(dict(reversed(list(case.items()))))

    def test_ac016_ties_are_broken_by_participant_id(self) -> None:
        """두 채무자의 금액이 같다 — 정렬 2차 키(ID 오름차순)가 없으면 여기서 흔들린다."""
        case = {"A": 1000, "B": -500, "C": -500}
        expected = [
            Transfer(from_participant_id="B", to_participant_id="A", amount_minor=500),
            Transfer(from_participant_id="C", to_participant_id="A", amount_minor=500),
        ]
        assert settle(case) == expected
        # 입력 순서를 어떻게 섞어도 같은 답이어야 한다.
        assert settle({"C": -500, "B": -500, "A": 1000}) == expected

    def test_ac016_ties_among_creditors_are_also_stable(self) -> None:
        case = {"A": 500, "B": 500, "C": -1000}
        assert settle(case) == settle({"B": 500, "C": -1000, "A": 500})
        assert [t.to_participant_id for t in settle(case)] == ["A", "B"]

    def test_ac048_amounts_are_integers_and_sum_to_zero(self) -> None:
        case = {"A": 2000, "B": -700, "C": -1300}
        transfers = settle(case)
        assert all(isinstance(t.amount_minor, int) for t in transfers)
        assert sum(t.amount_minor for t in transfers) == sum(v for v in case.values() if v > 0)

    def test_biggest_creditor_meets_biggest_debtor_first(self) -> None:
        """그리디 규칙 자체 — 매 단계에서 최소 한 명이 0 이 된다."""
        transfers = settle({"A": 300, "B": 100, "C": -250, "D": -150})
        assert transfers == [
            Transfer(from_participant_id="C", to_participant_id="A", amount_minor=250),
            Transfer(from_participant_id="D", to_participant_id="A", amount_minor=50),
            Transfer(from_participant_id="D", to_participant_id="B", amount_minor=100),
        ]

    def test_transfers_are_sorted_by_from_then_to(self) -> None:
        transfers = settle({"A": 300, "B": 100, "C": -250, "D": -150})
        keys = [(t.from_participant_id, t.to_participant_id) for t in transfers]
        assert keys == sorted(keys)

    def test_nonzero_total_is_rejected_loudly(self) -> None:
        """합이 0 이 아니면 분담 기록이 원금과 어긋난 것이다 — 조용히 계산하면 안 된다."""
        with pytest.raises(ValueError, match="잔액 합"):
            settle({"A": 100, "B": -50})


class TestEndToEnd:
    """AC-014 → AC-015 를 한 줄로 이어 본다 (경비 기록 → 잔액 → 송금)."""

    def test_ac014_ac015_pipeline(self) -> None:
        from harbor_lantern.domain.money import split_even

        shares_a = split_even(3000, ["A", "B", "C"])  # A 가 3000 을 셋이서
        shares_b = split_even(600, ["B", "C"])  # B 가 600 을 둘이서

        owed = {pid: shares_a.get(pid, 0) + shares_b.get(pid, 0) for pid in ("A", "B", "C")}
        result = balances({"A": 3000, "B": 600}, owed)

        assert result == {"A": 2000, "B": -700, "C": -1300}
        assert settle(result) == [
            Transfer(from_participant_id="B", to_participant_id="A", amount_minor=700),
            Transfer(from_participant_id="C", to_participant_id="A", amount_minor=1300),
        ]
