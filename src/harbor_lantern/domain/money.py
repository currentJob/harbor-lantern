"""금액 계산 — DSN-12 일부 (설계서 §6.5 · NFR-014 · AC-013 · AC-029 · AC-048).

**부동소수가 이 파일에 들어오면 안 된다.** 금액은 전부 정수 minor unit(HKD cent)이고
환율도 `rate_micro`(= 환율 × 1_000_000) 정수로 들고 다닌다. 0.1 을 백 번 더하면
10 이 아니라 9.99999999999998 이 되는데, 정산 합계가 0 이 아니게 되는 원인이 그것이다.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["MICRO", "hkd_cent_to_krw", "split_even"]

MICRO = 1_000_000  # rate_micro 의 스케일


def split_even(amount_minor: int, participant_ids: Sequence[str]) -> dict[str, int]:
    """균등 분담. 합계는 **항상** 원금과 정확히 일치한다 (AC-013).

    `1000 cent ÷ 3` → `334, 333, 333`. 나머지는 **참가자 ID 오름차순으로 앞에서부터
    1 씩** 얹는다 — 정렬 키가 ID 이므로 입력 순서가 달라도 결과가 같다(결정론).

    중복 ID 는 한 명으로 본다. 같은 사람을 두 번 넣었다고 두 몫을 물릴 수는 없다.
    """
    ids = sorted(set(participant_ids))
    if not ids:
        raise ValueError("분담 대상자가 비어 있다 — 호출 전에 422 로 거부해야 한다 (AC-012)")

    base, remainder = divmod(amount_minor, len(ids))
    return {pid: base + (1 if index < remainder else 0) for index, pid in enumerate(ids)}


def hkd_cent_to_krw(amount_minor: int, rate_micro: int) -> int:
    """HKD cent → KRW 원 (정수). 반올림은 half-up, **float 를 쓰지 않는다** (AC-029).

    `krw = (amount_minor * rate_micro + 50_000_000) // 100_000_000`

    - `amount_minor` 는 1/100 HKD, `rate_micro` 는 1/1_000_000 배율이므로 분모가 1e8 이다.
    - `+ 50_000_000` 은 그 분모의 절반 — 정수만으로 half-up 을 만드는 방법이다.

    검산: 10000 cent(=100 HKD) × 171.23 → `(10000 × 171_230_000 + 5e7) // 1e8 = 17123` 원.
    KRW 는 보조 단위가 없으므로 반환은 **원 단위**다.
    """
    if amount_minor < 0:
        raise ValueError("금액은 음수일 수 없다 (DDL CHECK(amount_minor > 0) 와 같은 규약)")
    if rate_micro <= 0:
        raise ValueError("환율은 양수여야 한다 — 환율이 없으면 환산 필드를 null 로 둔다 (AC-029)")
    return (amount_minor * rate_micro + MICRO * 100 // 2) // (MICRO * 100)
