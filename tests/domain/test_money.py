"""금액 — AC-013 · AC-029 · AC-048(금액 부분) (설계서 §6.5 · NFR-014).

부동소수가 끼어들면 합계가 원금과 1 cent 어긋나고, 그 1 cent 는 정산에서 잔액 합
0 을 깨뜨린다. 여기 있는 테스트는 전부 **정확 일치**만 본다 — `approx` 가 없다.
"""

from __future__ import annotations

import pytest

from harbor_lantern.domain.money import hkd_cent_to_krw, split_even


class TestSplitEven:
    """AC-013 — 나누어떨어지지 않아도 합계는 원금과 정확히 일치한다."""

    def test_ac013_1000_over_three(self) -> None:
        shares = split_even(1000, ["c", "a", "b"])
        assert shares == {"a": 334, "b": 333, "c": 333}
        assert sum(shares.values()) == 1000

    def test_ac013_remainder_goes_to_the_lowest_ids_deterministically(self) -> None:
        shares = split_even(1001, ["c", "a", "b"])
        assert shares == {"a": 334, "b": 334, "c": 333}

    def test_ac013_input_order_does_not_matter(self) -> None:
        """정렬 키가 참가자 ID 다 — 요청 본문의 순서가 분담액을 바꾸면 안 된다."""
        assert split_even(1000, ["a", "b", "c"]) == split_even(1000, ["c", "b", "a"])

    @pytest.mark.parametrize("amount", [1, 2, 3, 7, 100, 999, 1000, 1001, 123457])
    @pytest.mark.parametrize("people", [1, 2, 3, 5, 7])
    def test_ac013_sum_always_equals_the_original_amount(self, amount: int, people: int) -> None:
        ids = [f"p{index}" for index in range(people)]
        shares = split_even(amount, ids)
        assert sum(shares.values()) == amount
        assert len(shares) == people

    def test_ac013_shares_differ_by_at_most_one_cent(self) -> None:
        shares = split_even(1000, ["a", "b", "c"])
        assert max(shares.values()) - min(shares.values()) <= 1

    def test_exact_division_gives_equal_shares(self) -> None:
        assert split_even(900, ["a", "b", "c"]) == {"a": 300, "b": 300, "c": 300}

    def test_single_participant_takes_everything(self) -> None:
        assert split_even(1000, ["solo"]) == {"solo": 1000}

    def test_duplicate_ids_count_once(self) -> None:
        """같은 사람을 두 번 넣었다고 두 몫을 물릴 수는 없다."""
        assert split_even(1000, ["a", "a", "b"]) == split_even(1000, ["a", "b"])

    def test_zero_amount_splits_to_zeros(self) -> None:
        assert split_even(0, ["a", "b"]) == {"a": 0, "b": 0}

    def test_empty_participants_is_rejected(self) -> None:
        """AC-012 는 이 상황을 422 로 막는다 — 도메인은 조용히 0 을 만들지 않는다."""
        with pytest.raises(ValueError, match="분담 대상자"):
            split_even(1000, [])


class TestHkdCentToKrw:
    """AC-029 — `HKD cent × 환율` 을 정의된 반올림 규칙으로. float 없음."""

    def test_ac029_design_worked_example(self) -> None:
        """설계서 §6.5 검산: 10000 cent × 171.23 → 17123 원."""
        assert hkd_cent_to_krw(10000, 171_230_000) == 17123

    def test_ac029_rounds_half_up(self) -> None:
        # 1 cent × 150.0 = 1.5 원 → 2 원 (내림이면 1, 은행가 반올림이면 2 지만 2.5→2 에서 갈린다)
        assert hkd_cent_to_krw(1, 150_000_000) == 2
        assert hkd_cent_to_krw(1, 250_000_000) == 3  # 2.5 → 3 (은행가 반올림이면 2)

    def test_ac029_rounds_down_below_half(self) -> None:
        assert hkd_cent_to_krw(1, 149_000_000) == 1  # 1.49 → 1

    def test_ac029_zero_amount_is_zero(self) -> None:
        assert hkd_cent_to_krw(0, 171_230_000) == 0

    def test_ac029_result_is_an_int_in_won(self) -> None:
        """KRW 는 보조 단위가 없다 — cent 로 돌려주면 화면에 100배가 찍힌다."""
        value = hkd_cent_to_krw(100_000, 171_230_000)
        assert isinstance(value, int)
        assert value == 171_230  # 1000 HKD

    def test_ac048_large_amounts_stay_exact(self) -> None:
        """정수 연산이라 자릿수가 커져도 오차가 없다 (float 였다면 여기서 갈린다)."""
        assert hkd_cent_to_krw(999_999_999, 171_230_000) == (999_999_999 * 171_230_000 + 50_000_000) // 100_000_000

    def test_negative_amount_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="음수"):
            hkd_cent_to_krw(-1, 171_230_000)

    def test_non_positive_rate_is_rejected(self) -> None:
        """환율이 없으면 환산 필드를 null 로 둔다 — 0 을 넣어 0원으로 만들지 않는다(AC-029)."""
        with pytest.raises(ValueError, match="환율"):
            hkd_cent_to_krw(1000, 0)
