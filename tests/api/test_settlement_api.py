"""최소 송금 정산 (API) — AC-014 · AC-015 · AC-016 (REQ-008 · NFR-014).

알고리즘 자체는 `tests/domain/test_settle.py` 가 본다. 여기서는 **저장된 분담액에서
잔액이 제대로 굴러 나오는가**를 본다 — 정산이 재계산이 아니라 기록의 합이어야 드리프트가
생기지 않는다(§6.20).
"""

from __future__ import annotations

from tests.api.conftest import Actor, TripFixture


def _pay(trip: TripFixture, payer: Actor, amount: int, shares: list[Actor]):
    response = trip.client.post(
        f"{trip.base}/expenses",
        headers=trip.headers,
        json={
            "payer_id": payer.participant_id,
            "amount_minor": amount,
            "share_participant_ids": [actor.participant_id for actor in shares],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_settlement_of_an_empty_trip_is_all_zero(trip: TripFixture, guest: Actor) -> None:
    """AC-014 경계: 지출이 없으면 잔액이 전부 0 이고 송금은 빈 목록이다."""
    body = trip.client.get(f"{trip.base}/settlement", headers=trip.headers).json()
    assert body["currency"] == "HKD"
    assert [item["balance_minor"] for item in body["balances"]] == [0, 0]
    assert body["transfers"] == []


def test_balances_and_transfers_match_the_worked_example(trip: TripFixture) -> None:
    """AC-014: A `+2000` · B `-700` · C `-1300` → 송금 2건, 합계 0.

    설계서 §6.6 의 검산을 그대로 재현한다: C→A 1300, B→A 700.
    """
    b = trip.join("B")
    c = trip.join("C")
    a = trip.owner

    _pay(trip, a, 1400, [b, c])  # A +1400 / B -700 / C -700
    _pay(trip, a, 600, [c])  # A  +600 /         C -600

    body = trip.client.get(f"{trip.base}/settlement", headers=trip.headers).json()
    balances = {item["participant_id"]: item["balance_minor"] for item in body["balances"]}
    assert balances[a.participant_id] == 2000
    assert balances[b.participant_id] == -700
    assert balances[c.participant_id] == -1300
    assert sum(balances.values()) == 0

    transfers = {
        (item["from_participant_id"], item["to_participant_id"]): item["amount_minor"]
        for item in body["transfers"]
    }
    assert transfers == {
        (c.participant_id, a.participant_id): 1300,
        (b.participant_id, a.participant_id): 700,
    }


def test_paid_and_owed_are_reported_per_participant(trip: TripFixture) -> None:
    """AC-014: 낸 돈과 부담해야 할 돈이 각각 보인다(잔액 = 낸 돈 − 부담액)."""
    b = trip.join("B")
    _pay(trip, trip.owner, 1000, [trip.owner, b])

    body = trip.client.get(f"{trip.base}/settlement", headers=b.headers).json()
    rows = {item["participant_id"]: item for item in body["balances"]}
    assert rows[trip.owner.participant_id]["paid_minor"] == 1000
    assert rows[trip.owner.participant_id]["owed_minor"] == 500
    assert rows[trip.owner.participant_id]["balance_minor"] == 500
    assert rows[b.participant_id] == {
        "participant_id": b.participant_id,
        "display_name": "B",
        "paid_minor": 0,
        "owed_minor": 500,
        "balance_minor": -500,
    }


def test_transfer_count_is_at_most_n_minus_one(trip: TripFixture) -> None:
    """AC-015: 참가자 n 명에 대해 송금 건수는 n-1 이하다."""
    actors = [trip.owner, *(trip.join(f"참가자{index}") for index in range(4))]
    _pay(trip, actors[0], 9_999, actors)
    _pay(trip, actors[2], 4_321, actors[1:])
    _pay(trip, actors[4], 777, [actors[0], actors[4]])

    body = trip.client.get(f"{trip.base}/settlement", headers=trip.headers).json()
    assert len(body["transfers"]) <= len(actors) - 1
    assert sum(item["balance_minor"] for item in body["balances"]) == 0
    assert all(item["amount_minor"] >= 1 for item in body["transfers"])


def test_settlement_is_deterministic(trip: TripFixture) -> None:
    """AC-016: 같은 입력에 같은 결과다 — 정렬 규칙이 곧 결정론의 근거다."""
    actors = [trip.owner, *(trip.join(f"P{index}") for index in range(3))]
    _pay(trip, actors[1], 3_333, actors)
    _pay(trip, actors[3], 1_111, actors[:2])

    first = trip.client.get(f"{trip.base}/settlement", headers=trip.headers).json()
    second = trip.client.get(f"{trip.base}/settlement", headers=actors[2].headers).json()
    assert first == second


def test_deleting_an_expense_updates_the_settlement(trip: TripFixture) -> None:
    """AC-014 파생: 지출을 지우면 분담(CASCADE)과 잔액이 함께 사라진다."""
    b = trip.join("B")
    created = _pay(trip, trip.owner, 1000, [trip.owner, b])
    trip.client.delete(f"{trip.base}/expenses/{created['id']}", headers=trip.headers)

    body = trip.client.get(f"{trip.base}/settlement", headers=trip.headers).json()
    assert all(item["balance_minor"] == 0 for item in body["balances"])
    assert body["transfers"] == []
