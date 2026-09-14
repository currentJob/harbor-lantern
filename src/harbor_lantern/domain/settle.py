"""최소 송금 정산 — DSN-13 (설계서 §6.6 · REQ-008 · AC-014~AC-016 · NFR-014).

정수 minor unit 만 다룬다. 그리디는 매 단계에서 최소 한 명을 0 으로 만들므로
송금 건수는 **n-1 이하**다(AC-015).

**결정론이 이 모듈의 계약이다.** 같은 잔액에 항상 같은 송금 목록이 나와야 한다
(AC-016). 그리디에서 결정론을 보장하는 것은 오직 정렬 규칙 하나다 — 동액인 사람이
둘이면 그때부터 결과가 흔들리고, 그 실패는 **간헐적으로만** 재현된다.
"""

from __future__ import annotations

from collections.abc import Mapping

from harbor_lantern.domain.models import Transfer

__all__ = ["balances", "settle"]


def balances(paid: Mapping[str, int], owed: Mapping[str, int]) -> dict[str, int]:
    """`낸 돈 - 분담액` (AC-014). 두 맵에 등장하는 참가자 전원을 담는다.

    잔액이 0 인 사람도 빠뜨리지 않는다 — 화면의 정산 표에 "0" 으로 서 있어야
    "내 이름이 왜 없지"를 묻지 않는다.

    AC-014 검산: A 가 3000(A·B·C 균등 = 1000/1000/1000), B 가 600(B·C 균등 = 300/300).
    → paid A 3000 · B 600, owed A 1000 · B 1300 · C 1300
    → A `+2000`, B `-700`, C `-1300`, 합 0.
    """
    keys = sorted(set(paid) | set(owed))
    return {pid: paid.get(pid, 0) - owed.get(pid, 0) for pid in keys}


def settle(balance_by_participant: Mapping[str, int]) -> list[Transfer]:
    """최소 현금흐름 그리디 → 송금 목록 (AC-015 · AC-016).

    1. 채권자(잔액 > 0)·채무자(잔액 < 0)를 나눈다.
    2. 둘 다 **금액 내림차순, 동액이면 참가자 ID 오름차순**으로 정렬한다.
       이 두 번째 키가 없으면 동액 상황에서 결과가 실행마다 달라진다.
    3. 가장 큰 채권자와 가장 큰 채무자를 `min(a, b)` 만큼 상계하고 0 이 된 쪽을 뺀다.
    4. 결과를 `(from, to)` 오름차순으로 정렬해 돌려준다.

    잔액 합이 0 이 아니면 **ValueError** 다. 그 입력으로 계산을 계속하면 아무도
    보지 않는 잔돈이 남는데, 그건 정산이 아니라 반올림 사고의 증거다(NFR-014).
    """
    total = sum(balance_by_participant.values())
    if total != 0:
        raise ValueError(f"잔액 합이 0 이 아니다 ({total}) — 분담액 기록이 원금과 어긋났다 (NFR-014)")

    creditors = sorted(
        ((pid, amount) for pid, amount in balance_by_participant.items() if amount > 0),
        key=lambda item: (-item[1], item[0]),
    )
    debtors = sorted(
        ((pid, -amount) for pid, amount in balance_by_participant.items() if amount < 0),
        key=lambda item: (-item[1], item[0]),
    )

    transfers: list[Transfer] = []
    credit_index = 0
    debit_index = 0
    credit_left = creditors[credit_index][1] if creditors else 0
    debit_left = debtors[debit_index][1] if debtors else 0

    while credit_index < len(creditors) and debit_index < len(debtors):
        amount = min(credit_left, debit_left)
        transfers.append(
            Transfer(
                from_participant_id=debtors[debit_index][0],
                to_participant_id=creditors[credit_index][0],
                amount_minor=amount,
            )
        )
        credit_left -= amount
        debit_left -= amount
        if credit_left == 0:
            credit_index += 1
            credit_left = creditors[credit_index][1] if credit_index < len(creditors) else 0
        if debit_left == 0:
            debit_index += 1
            debit_left = debtors[debit_index][1] if debit_index < len(debtors) else 0

    transfers.sort(key=lambda t: (t.from_participant_id, t.to_participant_id))
    return transfers
