"""경비 기록과 원화 환산 — AC-012 · AC-013 · AC-029 (REQ-007 · REQ-015 · NFR-014).

금액은 전부 **정수 minor unit**(HKD cent)이다. 분담액은 기록 시점에 확정 저장하고,
합계는 원금과 **정확히** 일치해야 한다 — 1센트가 새면 정산이 영원히 안 맞는다.
"""

from __future__ import annotations

from typing import Any

from tests.api.conftest import Actor, TripFixture


def _create(trip: TripFixture, **overrides: Any):
    payload = {
        "payer_id": trip.owner.participant_id,
        "amount_minor": 10_000,
        "share_participant_ids": [trip.owner.participant_id],
        "note": "딤섬",
    }
    payload.update(overrides)
    return trip.client.post(f"{trip.base}/expenses", headers=trip.headers, json=payload)


def test_create_stores_payer_amount_currency_note_and_shares(trip: TripFixture, guest: Actor) -> None:
    """AC-012: 지불자·금액·통화·내역·분담 대상자가 저장된다."""
    response = _create(trip, share_participant_ids=[trip.owner.participant_id, guest.participant_id])
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["payer_id"] == trip.owner.participant_id
    assert body["amount_minor"] == 10_000
    assert body["currency"] == "HKD"
    assert body["note"] == "딤섬"
    assert body["version"] == 1
    assert {share["participant_id"] for share in body["shares"]} == {
        trip.owner.participant_id,
        guest.participant_id,
    }
    assert sum(share["share_minor"] for share in body["shares"]) == 10_000


def test_zero_or_negative_amount_is_422(trip: TripFixture) -> None:
    """AC-012: 금액이 0 이하면 422 다."""
    assert _create(trip, amount_minor=0).status_code == 422
    assert _create(trip, amount_minor=-1).status_code == 422
    assert trip.client.get(f"{trip.base}/expenses", headers=trip.headers).json()["items"] == []


def test_empty_share_list_is_422(trip: TripFixture) -> None:
    """AC-012: 분담 대상자가 비어 있으면 422 다."""
    assert _create(trip, share_participant_ids=[]).status_code == 422


def test_outsider_payer_or_share_is_422(trip: TripFixture, client: Any) -> None:
    """AC-012 경계: 그 여행의 참가자가 아니면 422 다(다른 여행의 참가자 ID 포함)."""
    other = client.post("/api/trips", json={}).json()
    outsider = other["participant"]["id"]

    assert _create(trip, payer_id=outsider).status_code == 422
    response = _create(trip, share_participant_ids=[trip.owner.participant_id, outsider])
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_participant"


def test_remainder_goes_to_the_first_ids_in_order(trip: TripFixture) -> None:
    """AC-013 · NFR-014: `1000 ÷ 3 → 334·333·333`, 합계는 정확히 원금이다.

    나머지는 참가자 ID 오름차순으로 앞에서부터 +1 이다 — 입력 순서가 달라도 결과가 같다.
    """
    second = trip.join("둘")
    third = trip.join("셋")
    ids = sorted([trip.owner.participant_id, second.participant_id, third.participant_id])

    body = _create(trip, amount_minor=1000, share_participant_ids=list(reversed(ids))).json()
    shares = {share["participant_id"]: share["share_minor"] for share in body["shares"]}

    assert [shares[participant_id] for participant_id in ids] == [334, 333, 333]
    assert sum(shares.values()) == 1000


def test_amount_krw_uses_integer_half_up_conversion(trip: TripFixture, fx_port: Any) -> None:
    """AC-029: `(cent × rate_micro + 50_000_000) // 100_000_000` — 부동소수 없음.

    검산: 10000 cent × 171.23 = 17123 원.
    """
    body = _create(trip, amount_minor=10_000).json()
    assert body["amount_krw"] == 17_123

    listing = trip.client.get(f"{trip.base}/expenses", headers=trip.headers).json()
    assert listing["total_minor"] == 10_000
    assert listing["total_krw"] == 17_123
    assert listing["fx"]["available"] is True
    assert listing["fx"]["rate_micro"] == 171_230_000
    assert listing["fx"]["rate_date"] == "2026-09-11"
    assert fx_port.calls == 1


def test_amount_krw_is_null_when_fx_unavailable(trip: TripFixture) -> None:
    """AC-029 후반: 환율이 `available:false` 면 환산은 null 이고 **원금은 정상**이다.

    (이 테스트는 가짜 환율을 꽂지 않는다 — 진짜 어댑터가 네트워크 차단에 막혀
    실패하는 경로를 그대로 탄다. 그것이 운영에서 외부가 죽었을 때의 모습이다.)
    """
    body = _create(trip, amount_minor=10_000).json()
    assert body["amount_minor"] == 10_000
    assert body["amount_krw"] is None

    listing = trip.client.get(f"{trip.base}/expenses", headers=trip.headers).json()
    assert listing["total_krw"] is None
    assert listing["fx"]["available"] is False
    assert listing["items"][0]["amount_minor"] == 10_000


def test_list_is_ordered_and_summed(trip: TripFixture, guest: Actor) -> None:
    """AC-012: 목록에 전부 담기고 합계가 맞는다."""
    _create(trip, amount_minor=500, note="첫째")
    _create(trip, amount_minor=1_500, payer_id=guest.participant_id, note="둘째")

    listing = trip.client.get(f"{trip.base}/expenses", headers=guest.headers).json()
    # 고정 시계라 `spent_at` 이 같다 — 그때의 2차 정렬 키는 ID 이므로 순서를 단언하지
    # 않는다. 여기서 보는 것은 "전부 담겼는가"다.
    assert {item["note"] for item in listing["items"]} == {"첫째", "둘째"}
    assert listing["total_minor"] == 2_000
    assert listing["currency"] == "HKD"


def test_state_summary_tracks_expenses(trip: TripFixture) -> None:
    """AC-012 파생: `/state` 의 `expenses_summary` 가 건수·합계를 그대로 반영한다."""
    _create(trip, amount_minor=2_500)
    _create(trip, amount_minor=700)
    assert trip.state().json()["expenses_summary"] == {"count": 2, "total_minor": 3_200, "currency": "HKD"}


def test_update_requires_matching_version(trip: TripFixture) -> None:
    """AC-047 (경비): 낡은 `version` 으로 수정하면 409 이고 값이 안 바뀐다."""
    created = _create(trip, amount_minor=1_000).json()
    expense_id = created["id"]

    ok = trip.client.patch(
        f"{trip.base}/expenses/{expense_id}",
        headers=trip.headers,
        json={"version": created["version"], "amount_minor": 2_000, "note": "정정"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["amount_minor"] == 2_000
    assert ok.json()["version"] == created["version"] + 1

    stale = trip.client.patch(
        f"{trip.base}/expenses/{expense_id}",
        headers=trip.headers,
        json={"version": created["version"], "amount_minor": 9_999},
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "version_conflict"

    listing = trip.client.get(f"{trip.base}/expenses", headers=trip.headers).json()
    assert listing["items"][0]["amount_minor"] == 2_000


def test_update_reallocates_shares(trip: TripFixture, guest: Actor) -> None:
    """AC-013: 금액·분담자가 바뀌면 분담액을 **다시 확정 저장**한다(합계는 늘 원금)."""
    created = _create(trip, amount_minor=1_000).json()
    updated = trip.client.patch(
        f"{trip.base}/expenses/{created['id']}",
        headers=trip.headers,
        json={
            "version": created["version"],
            "amount_minor": 1_001,
            "share_participant_ids": [trip.owner.participant_id, guest.participant_id],
        },
    ).json()
    assert sum(share["share_minor"] for share in updated["shares"]) == 1_001
    assert len(updated["shares"]) == 2


def test_delete_removes_expense_and_its_shares(trip: TripFixture) -> None:
    """AC-012 파생: 삭제하면 목록에서 사라지고 분담도 함께 사라진다(CASCADE)."""
    created = _create(trip).json()
    assert trip.client.delete(f"{trip.base}/expenses/{created['id']}", headers=trip.headers).status_code == 204
    listing = trip.client.get(f"{trip.base}/expenses", headers=trip.headers).json()
    assert listing["items"] == []
    assert listing["total_minor"] == 0
    assert trip.state().json()["expenses_summary"]["count"] == 0


def test_unknown_expense_is_404(trip: TripFixture) -> None:
    """AC-012 경계."""
    assert trip.client.delete(f"{trip.base}/expenses/없는경비", headers=trip.headers).status_code == 404
    patch = trip.client.patch(f"{trip.base}/expenses/없는경비", headers=trip.headers, json={"version": 1})
    assert patch.status_code == 404
