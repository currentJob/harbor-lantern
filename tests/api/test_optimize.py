"""동선 최적화 제안 (API) — AC-017 · AC-018 (REQ-009 · DSN-19).

**이 엔드포인트는 쓰기를 하지 않는다.** 제안만 돌려주고 적용은 재정렬이 한다 —
쓰기 경로를 하나로 유지하는 것이 §6.11 의 원자성 설계를 지키는 방법이다.
"""

from __future__ import annotations

import pytest

from tests.api.conftest import TripFixture


@pytest.mark.parametrize("day_index", [1, 2, 3, 4])
def test_proposal_never_increases_total_distance(trip: TripFixture, day_index: int) -> None:
    """AC-017: 제안의 총 이동거리는 현재 순서보다 **크지 않다**(부등식이 깨지지 않는다)."""
    response = trip.client.post(f"{trip.base}/days/{day_index}/optimize", headers=trip.headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["day_index"] == day_index
    assert body["proposed_total_distance_m"] <= body["current_total_distance_m"]
    assert body["improved"] is (body["proposed_order"] != body["current_order"])
    assert body["algorithm"] in {"exact", "two_opt"}


@pytest.mark.parametrize("day_index", [1, 2, 3, 4])
def test_proposal_is_a_permutation_of_the_current_order(trip: TripFixture, day_index: int) -> None:
    """AC-017: 입력 집합과 동일하다 — 누락도 중복도 없다.

    스팟이 조용히 빠진 제안을 적용하면 그 스팟은 화면에서 사라진다.
    """
    body = trip.client.post(f"{trip.base}/days/{day_index}/optimize", headers=trip.headers).json()
    assert body["current_order"] == trip.spot_ids(day_index)
    assert sorted(body["proposed_order"]) == sorted(body["current_order"])
    assert len(set(body["proposed_order"])) == len(body["proposed_order"])


def test_anchored_spot_keeps_its_index(trip: TripFixture) -> None:
    """AC-018: 고정시각 스팟(앵커)은 **현재 인덱스 자리를 유지**한다.

    Day 1 의 `20:00` 스팟이 그 자리에서 움직이면 "밤 8시 일정"이 오후로 끌려간다.
    """
    body = trip.client.post(f"{trip.base}/days/1/optimize", headers=trip.headers).json()
    assert len(body["anchored_spot_ids"]) == 1

    for anchor in body["anchored_spot_ids"]:
        assert body["proposed_order"].index(anchor) == body["current_order"].index(anchor)


def test_proposal_is_deterministic(trip: TripFixture) -> None:
    """AC-018: 같은 입력에 같은 출력 — 동률 타이브레이크가 고정돼 있다."""
    first = trip.client.post(f"{trip.base}/days/2/optimize", headers=trip.headers).json()
    second = trip.client.post(f"{trip.base}/days/2/optimize", headers=trip.headers).json()
    assert first == second


def test_optimize_does_not_write(trip: TripFixture) -> None:
    """AC-017: 제안은 저장되지 않는다 — 순서도 리비전도 그대로다."""
    before = trip.spot_ids(2)
    revision = trip.revision()
    trip.client.post(f"{trip.base}/days/2/optimize", headers=trip.headers)
    assert trip.spot_ids(2) == before
    assert trip.revision() == revision


def test_applying_the_proposal_shortens_the_day(trip: TripFixture) -> None:
    """AC-017(적용): 제안을 재정렬로 적용하면 `/state` 의 총 거리가 제안값과 같아진다."""
    proposal = trip.client.post(f"{trip.base}/days/2/optimize", headers=trip.headers).json()
    before_distance = trip.day(2)["totals"]["distance_m"]
    assert proposal["current_total_distance_m"] == pytest.approx(before_distance)

    applied = trip.client.put(
        f"{trip.base}/days/2/order",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "spot_ids": proposal["proposed_order"]},
    )
    assert applied.status_code == 200, applied.text

    after_distance = trip.day(2)["totals"]["distance_m"]
    assert after_distance == pytest.approx(proposal["proposed_total_distance_m"])
    assert after_distance <= before_distance


def test_single_spot_day_has_nothing_to_improve(trip: TripFixture) -> None:
    """AC-017 경계: 스팟이 1개면 거리가 0 이고 `improved` 는 false 다(0 으로 나누지 않는다)."""
    for spot_id in trip.spot_ids(4)[1:]:
        trip.client.delete(f"{trip.base}/spots/{spot_id}", headers=trip.headers)

    body = trip.client.post(f"{trip.base}/days/4/optimize", headers=trip.headers).json()
    assert body["current_total_distance_m"] == 0
    assert body["proposed_total_distance_m"] == 0
    assert body["improved"] is False
    assert len(body["proposed_order"]) == 1


def test_optimize_requires_access(trip: TripFixture, client) -> None:
    """AC-017 경계: 토큰이 없으면 404 다."""
    assert client.post(f"{trip.base}/days/1/optimize").status_code == 404
