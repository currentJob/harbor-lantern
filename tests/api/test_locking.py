"""낙관적 잠금과 두 카운터의 구분 — AC-047 (NFR-012 · 설계서 §5.2 · §12 F3 · F5).

| 이름 | 어디에 | 누가 보내나 |
|------|--------|------------|
| `spot.version` · `expense.version` | 리소스 행 | `PATCH` 본문의 `version` |
| `trip.revision` | 여행 행 | 재정렬·이동 본문의 `expected_revision` |

**둘 다 정수라 바꿔 써도 타입 검사가 안 잡는다.** 바꿔 쓰면 409 가 아예 안 나거나
정상 요청마다 409 가 난다. 그래서 "서로의 값을 넣으면 어떻게 되는가"를 직접 테스트한다.
"""

from __future__ import annotations

from typing import Any

from tests.api.conftest import Actor, TripFixture


def _first_spot(trip: TripFixture) -> dict[str, Any]:
    return trip.day(1)["spots"][0]


def test_stale_version_returns_409_with_the_latest_resource(trip: TripFixture, guest: Actor) -> None:
    """AC-047: 낡은 `version` 으로 수정하면 409 + **최신 리소스**, 데이터는 안 덮어써진다."""
    spot = _first_spot(trip)
    original_name = spot["name"]

    won = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=guest.headers,
        json={"version": spot["version"], "name": "B 가 먼저 저장했다"},
    )
    assert won.status_code == 200, won.text

    lost = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": spot["version"], "name": "A 가 늦게 저장했다"},
    )
    assert lost.status_code == 409
    body = lost.json()
    assert body["error"] == "version_conflict"
    assert body["detail"]["current"]["name"] == "B 가 먼저 저장했다"
    assert body["detail"]["current"]["version"] == spot["version"] + 1

    fresh = _first_spot(trip)
    assert fresh["name"] == "B 가 먼저 저장했다" != original_name
    assert fresh["version"] == spot["version"] + 1


def test_retrying_with_the_returned_version_succeeds(trip: TripFixture) -> None:
    """AC-047: 409 가 준 최신 버전으로 다시 시도하면 통과한다(재시도 경로가 막히지 않는다)."""
    spot = _first_spot(trip)
    trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": spot["version"], "tip": "첫 수정"},
    )
    conflict = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": spot["version"], "tip": "두 번째 수정"},
    )
    latest = conflict.json()["detail"]["current"]["version"]

    retried = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": latest, "tip": "두 번째 수정"},
    )
    assert retried.status_code == 200
    assert _first_spot(trip)["tip"] == "두 번째 수정"


def test_patch_rejects_expected_revision(trip: TripFixture) -> None:
    """§12 F3: `PATCH` 는 `expected_revision` 을 **받지 않는다**(계약 밖 필드 → 422).

    조용히 무시하면 클라이언트는 잠금이 걸린 줄 알고 덮어쓰기를 계속한다.
    """
    spot = _first_spot(trip)
    response = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "name": "혼동"},
    )
    assert response.status_code == 422
    assert _first_spot(trip)["name"] == spot["name"]


def test_reorder_rejects_version(trip: TripFixture) -> None:
    """§12 F3: 재정렬은 `version` 을 **받지 않는다** — 여러 행을 한꺼번에 바꾸므로
    행 단위 잠금은 의미가 없고 여행 단위 `expected_revision` 이 정확하다."""
    ids = trip.spot_ids(1)
    response = trip.client.put(
        f"{trip.base}/days/1/order",
        headers=trip.headers,
        json={"version": 1, "spot_ids": list(reversed(ids))},
    )
    assert response.status_code == 422
    assert trip.spot_ids(1) == ids


def test_using_the_revision_as_a_version_is_a_conflict(trip: TripFixture) -> None:
    """§12 F3: 리비전 값을 `version` 자리에 넣으면 **409 로 시끄럽게** 실패한다.

    두 값이 우연히 같아 통과하는 일을 막으려고, 먼저 리비전만 3번 올려 둔다.
    """
    for index in range(3):
        trip.client.post(
            f"{trip.base}/days/2/spots",
            headers=trip.headers,
            json={"name": f"채우기 {index}", "lat": 22.3, "lng": 114.2},
        )
    spot = _first_spot(trip)
    revision = trip.revision()
    assert revision != spot["version"], "이 테스트가 의미를 가지려면 두 값이 달라야 한다"

    response = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": revision, "name": "잘못된 카운터"},
    )
    assert response.status_code == 409
    assert _first_spot(trip)["name"] == spot["name"]


def test_using_a_version_as_the_expected_revision_is_a_conflict(trip: TripFixture) -> None:
    """§12 F3: 반대 방향 — 스팟 버전을 `expected_revision` 자리에 넣으면 409 다."""
    for index in range(3):
        trip.client.post(
            f"{trip.base}/days/2/spots",
            headers=trip.headers,
            json={"name": f"채우기 {index}", "lat": 22.3, "lng": 114.2},
        )
    ids = trip.spot_ids(1)
    spot_version = _first_spot(trip)["version"]
    assert spot_version != trip.revision()

    response = trip.client.put(
        f"{trip.base}/days/1/order",
        headers=trip.headers,
        json={"expected_revision": spot_version, "spot_ids": list(reversed(ids))},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "revision_conflict"
    assert trip.spot_ids(1) == ids


def test_version_only_patch_still_bumps(trip: TripFixture) -> None:
    """AC-047 경계: 필드 없이 `version` 만 보내면 값은 그대로고 버전만 오른다."""
    spot = _first_spot(trip)
    response = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": spot["version"]},
    )
    assert response.status_code == 200
    assert response.json()["version"] == spot["version"] + 1
    assert response.json()["name"] == spot["name"]


def test_failed_reorder_leaves_no_partial_write(trip: TripFixture, app: Any) -> None:
    """AC-047: 재정렬이 실패하면 **DB 에도** 부분 반영이 없다(0..n-1 그대로).

    응답만 보면 롤백을 확신할 수 없다 — 중간 상태가 남았는지는 테이블을 봐야 안다.
    """
    day_ids = trip.spot_ids(2)
    response = trip.client.put(
        f"{trip.base}/days/2/order",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "spot_ids": [*day_ids[:-1], "존재하지-않는-스팟"]},
    )
    assert response.status_code == 422

    with app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT spot.id AS id, spot.position AS position FROM spot"
            " JOIN day ON day.id = spot.day_id"
            " WHERE day.trip_id = ? AND day.day_index = 2 ORDER BY spot.position",
            (trip.trip_id,),
        ).fetchall()
    assert [row["id"] for row in rows] == day_ids
    assert [row["position"] for row in rows] == list(range(len(day_ids)))


def test_deleting_a_spot_leaves_no_orphan_rows(trip: TripFixture, app: Any) -> None:
    """§12 F5: `PRAGMA foreign_keys` 가 연결마다 켜져 있어야 CASCADE 가 돈다.

    켜지 않아도 모든 테스트가 통과한다 — 다만 `visit` 고아 행이 조용히 쌓이고
    진행률 분자가 분모를 넘는 날이 온다. 그래서 **테이블을 직접 센다.**
    """
    spot_id = trip.spot_ids(1)[0]
    trip.client.put(f"{trip.base}/spots/{spot_id}/done", headers=trip.headers, json={"done": True})
    trip.client.post(
        f"{trip.base}/expenses",
        headers=trip.headers,
        json={
            "payer_id": trip.owner.participant_id,
            "amount_minor": 1_000,
            "spot_id": spot_id,
            "share_participant_ids": [trip.owner.participant_id],
        },
    )

    assert trip.client.delete(f"{trip.base}/spots/{spot_id}", headers=trip.headers).status_code == 204

    with app.state.db.connection() as conn:
        visits = conn.execute("SELECT COUNT(*) AS n FROM visit WHERE spot_id = ?", (spot_id,)).fetchone()["n"]
        expenses = conn.execute("SELECT spot_id FROM expense").fetchall()
    assert visits == 0, "visit 고아 행이 남았다 — foreign_keys 가 꺼져 있다"
    # 경비는 남되 스팟 참조만 끊긴다 (`ON DELETE SET NULL`) — 지출 기록 자체를 지우면
    # 정산이 조용히 틀어진다.
    assert [row["spot_id"] for row in expenses] == [None]
    assert trip.client.get(f"{trip.base}/expenses", headers=trip.headers).json()["total_minor"] == 1_000
