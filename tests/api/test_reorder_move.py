"""일자 내 재정렬과 일자 간 이동 — AC-008 · AC-009 · AC-047 (REQ-005 · NFR-012).

**§12 F2 가 이 파일의 존재 이유다.** 재정렬을 한 번에 UPDATE 하면
`UNIQUE(day_id, position)` 이 중간 상태에서 터지는데, 스팟이 2개일 때는 우연히 통과하고
**3개 이상 · 특정 순열에서만** 실패한다. 그래서 한 순열이 아니라 **전 순열**을 돌린다.
"""

from __future__ import annotations

from itertools import permutations

import pytest

from tests.api.conftest import TripFixture


def _reorder(trip: TripFixture, day_index: int, spot_ids: list[str], revision: int | None = None):
    return trip.client.put(
        f"{trip.base}/days/{day_index}/order",
        headers=trip.headers,
        json={
            "expected_revision": trip.revision() if revision is None else revision,
            "spot_ids": spot_ids,
        },
    )


def test_moving_third_spot_to_front(trip: TripFixture) -> None:
    """AC-008: 3번 스팟을 1번 자리로 옮기면 `[3,1,2,...]` 가 되고 순서값에 빈 자리가 없다."""
    before = trip.spot_ids(1)
    desired = [before[2], before[0], before[1], *before[3:]]

    response = _reorder(trip, 1, desired)
    assert response.status_code == 200, response.text
    assert response.json()["spot_ids"] == desired

    day = trip.day(1)
    assert [spot["id"] for spot in day["spots"]] == desired
    assert [spot["position"] for spot in day["spots"]] == list(range(len(desired)))


@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_every_permutation_of_a_four_spot_day(trip: TripFixture, order: tuple[int, ...]) -> None:
    """AC-008 · §12 F2: 4스팟 일자의 **24개 순열 전부**가 UNIQUE 충돌 없이 적용된다.

    음수 대피 → 0..n-1 재부여의 2단계가 빠지면 여기서 `IntegrityError` 가 난다.
    """
    base = trip.spot_ids(4)
    desired = [base[index] for index in order]

    response = _reorder(trip, 4, desired)
    assert response.status_code == 200, response.text

    day = trip.day(4)
    assert [spot["id"] for spot in day["spots"]] == desired
    assert [spot["position"] for spot in day["spots"]] == [0, 1, 2, 3]


def test_repeated_reorders_stay_consistent(trip: TripFixture) -> None:
    """AC-008: 같은 일자를 연달아 재정렬해도 순서값이 계속 0..n-1 이다(누적 오염 없음)."""
    for rotation in range(1, 7):
        current = trip.spot_ids(1)
        desired = current[rotation % len(current) :] + current[: rotation % len(current)]
        assert _reorder(trip, 1, desired).status_code == 200
        day = trip.day(1)
        assert [spot["id"] for spot in day["spots"]] == desired
        assert [spot["position"] for spot in day["spots"]] == list(range(6))


def test_reorder_bumps_revision(trip: TripFixture) -> None:
    """AC-035: 재정렬도 변경이므로 리비전이 오른다."""
    before = trip.revision()
    ids = trip.spot_ids(2)
    response = _reorder(trip, 2, list(reversed(ids)), revision=before)
    assert response.json()["revision"] == before + 1
    assert trip.revision() == before + 1


@pytest.mark.parametrize("kind", ["missing", "duplicate", "foreign", "extra"])
def test_bad_spot_set_is_422_and_writes_nothing(trip: TripFixture, kind: str) -> None:
    """AC-047: 집합이 어긋나면 422 이고 **부분 반영이 없다** — 순서도 리비전도 그대로다."""
    before = trip.spot_ids(3)
    revision_before = trip.revision()
    candidates = {
        "missing": before[:-1],
        "duplicate": [before[0], *before[:-1]],
        "foreign": [*before[:-1], trip.spot_ids(1)[0]],
        "extra": [*before, before[0]],
    }

    response = _reorder(trip, 3, candidates[kind])
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "spot_set_mismatch"
    assert trip.spot_ids(3) == before
    assert trip.revision() == revision_before


def test_stale_expected_revision_is_409(trip: TripFixture) -> None:
    """AC-047 · §12 F3: 재정렬은 `expected_revision`(여행 동기화)으로 막는다.

    409 본문에는 **최신 리비전**이 실린다 — 클라이언트가 받아 다시 시도할 수 있게.
    """
    stale = trip.revision()
    # 다른 참가자가 먼저 무언가를 바꿨다 — 내가 들고 있는 리비전이 낡았다.
    trip.client.put(
        f"{trip.base}/spots/{trip.spot_ids(1)[0]}/done",
        headers=trip.headers,
        json={"done": True},
    )
    ids = trip.spot_ids(2)
    current = trip.revision()
    assert current > stale

    response = _reorder(trip, 2, list(reversed(ids)), revision=stale)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "revision_conflict"
    assert body["detail"]["current_revision"] == current
    assert trip.spot_ids(2) == ids


def test_move_between_days_renumbers_both(trip: TripFixture) -> None:
    """AC-009: Day 1 → Day 3 이동 후 양쪽 모두 0부터 연속이고 개수가 ∓1 이다."""
    day_one = trip.spot_ids(1)
    day_three = trip.spot_ids(3)
    spot_id = day_one[1]

    response = trip.client.post(
        f"{trip.base}/spots/{spot_id}/move",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "to_day_index": 3},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "spot_id": spot_id,
        "from_day_index": 1,
        "to_day_index": 3,
        "revision": body["revision"],
    }

    after_one = trip.day(1)
    after_three = trip.day(3)
    assert len(after_one["spots"]) == len(day_one) - 1
    assert len(after_three["spots"]) == len(day_three) + 1
    assert [spot["position"] for spot in after_one["spots"]] == list(range(len(day_one) - 1))
    assert [spot["position"] for spot in after_three["spots"]] == list(range(len(day_three) + 1))
    assert [spot["id"] for spot in after_three["spots"]][-1] == spot_id
    assert [spot["day_index"] for spot in after_three["spots"]] == [3] * (len(day_three) + 1)


def test_move_to_explicit_position(trip: TripFixture) -> None:
    """AC-009: `to_position` 을 주면 대상 일자의 그 자리에 꽂힌다.

    옮겨 온 스팟의 대피 position 이 대상 일자의 것과 겹치면 UNIQUE 가 터진다 —
    두 일자에 **다른 대피 오프셋**을 쓰는 이유다.
    """
    source = trip.spot_ids(2)
    target = trip.spot_ids(4)
    spot_id = source[0]

    response = trip.client.post(
        f"{trip.base}/spots/{spot_id}/move",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "to_day_index": 4, "to_position": 0},
    )
    assert response.status_code == 200, response.text
    assert trip.spot_ids(4) == [spot_id, *target]
    assert trip.spot_ids(2) == source[1:]


def test_move_within_the_same_day(trip: TripFixture) -> None:
    """AC-009 경계: 같은 일자 안에서의 이동도 0..n-1 을 유지한다."""
    ids = trip.spot_ids(3)
    response = trip.client.post(
        f"{trip.base}/spots/{ids[-1]}/move",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "to_day_index": 3, "to_position": 0},
    )
    assert response.status_code == 200, response.text
    assert trip.spot_ids(3) == [ids[-1], *ids[:-1]]
    assert [spot["position"] for spot in trip.day(3)["spots"]] == list(range(len(ids)))


def test_move_with_stale_revision_is_409_and_changes_nothing(trip: TripFixture) -> None:
    """AC-047: 이동도 `expected_revision` 으로 막는다."""
    source = trip.spot_ids(1)
    target = trip.spot_ids(2)
    response = trip.client.post(
        f"{trip.base}/spots/{source[0]}/move",
        headers=trip.headers,
        json={"expected_revision": trip.revision() + 7, "to_day_index": 2},
    )
    assert response.status_code == 409
    assert trip.spot_ids(1) == source
    assert trip.spot_ids(2) == target


def test_move_unknown_spot_is_404(trip: TripFixture) -> None:
    """AC-009 경계: 없는 스팟 이동은 404 다."""
    response = trip.client.post(
        f"{trip.base}/spots/없는스팟/move",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "to_day_index": 2},
    )
    assert response.status_code == 404
