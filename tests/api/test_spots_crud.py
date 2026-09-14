"""스팟 생성·수정·삭제 — AC-006 · AC-007 (REQ-004 · NFR-012).

낙관적 잠금(409)은 `test_locking.py` 가 따로 본다. 여기서는 **편집 결과가 이후 조회에
반영되는가**와 **삭제가 진행률 분모를 줄이는가**를 본다.
"""

from __future__ import annotations

from typing import Any

from tests.api.conftest import TripFixture

NEW_SPOT = {
    "name": "빅토리아 공원",
    "name_original": "Victoria Park",
    "time_label": "오후",
    "lat": 22.2823,
    "lng": 114.1885,
}


def test_create_appends_to_end_of_day(trip: TripFixture) -> None:
    """AC-006: 201 + 생성된 리소스, 해당 일자 **목록 끝**에 추가된다."""
    before = trip.spot_ids(1)
    response = trip.client.post(f"{trip.base}/days/1/spots", headers=trip.headers, json=NEW_SPOT)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == NEW_SPOT["name"]
    assert created["day_index"] == 1
    assert created["position"] == len(before)
    assert created["version"] == 1
    assert created["done"] == {"is_done": False, "by_participant_id": None, "by_display_name": None, "at": None}
    assert trip.spot_ids(1) == [*before, created["id"]]


def test_created_spot_carries_directions_url_with_exact_coordinates(trip: TripFixture) -> None:
    """AC-006 파생 · AC-034: 길찾기 URL 좌표가 저장값과 정확히 같다(반올림 금지)."""
    created = trip.client.post(f"{trip.base}/days/2/spots", headers=trip.headers, json=NEW_SPOT).json()
    assert created["directions_url"] == (
        f"https://www.google.com/maps/dir/?api=1&destination={NEW_SPOT['lat']!r},{NEW_SPOT['lng']!r}"
    )


def test_out_of_range_coordinates_are_rejected(trip: TripFixture) -> None:
    """AC-006: 위도 [-90,90] · 경도 [-180,180] 밖이면 422 이고 아무것도 만들어지지 않는다."""
    before = len(trip.spot_ids(1))
    for payload in ({**NEW_SPOT, "lat": 91.0}, {**NEW_SPOT, "lng": -181.0}):
        response = trip.client.post(f"{trip.base}/days/1/spots", headers=trip.headers, json=payload)
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "validation_error"
    assert len(trip.spot_ids(1)) == before


def test_unknown_field_is_rejected_rather_than_ignored(trip: TripFixture) -> None:
    """AC-006 경계: 계약에 없는 필드는 422 다.

    조용히 무시하면 "고쳤는데 안 바뀐다"가 되고, 그건 사용자가 서버를 불신하게 만든다.
    """
    response = trip.client.post(
        f"{trip.base}/days/1/spots", headers=trip.headers, json={**NEW_SPOT, "colour": "red"}
    )
    assert response.status_code == 422


def test_day_index_outside_one_to_four_is_rejected(trip: TripFixture) -> None:
    """AC-006 경계: 일자 번호는 1~4 다."""
    assert trip.client.post(f"{trip.base}/days/9/spots", headers=trip.headers, json=NEW_SPOT).status_code == 422


def test_update_is_reflected_in_later_reads(trip: TripFixture) -> None:
    """AC-007: 영업시간·휴무·이름 수정이 이후 조회에 반영된다."""
    spot_id = trip.spot_ids(1)[0]
    original = next(spot for spot in trip.day(1)["spots"] if spot["id"] == spot_id)
    changes = {
        "version": original["version"],
        "name": "이름을 바꿨다",
        "hours_text": "매일 10:00–18:00",
        "closed_text": "화요일",
    }
    response = trip.client.patch(f"{trip.base}/spots/{spot_id}", headers=trip.headers, json=changes)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["version"] == original["version"] + 1

    fresh = next(spot for spot in trip.day(1)["spots"] if spot["id"] == spot_id)
    assert fresh["name"] == "이름을 바꿨다"
    assert fresh["hours_text"] == "매일 10:00–18:00"
    assert fresh["hours"]["status"] == "open_range"
    assert fresh["hours"]["open_local"] == "10:00"
    assert fresh["closed_weekdays"] == [1]  # 0=월 … 화요일 = 1


def test_partial_update_leaves_other_fields_alone(trip: TripFixture) -> None:
    """AC-007: **준 필드만** 갱신한다 — 안 보낸 필드가 초기화되면 안 된다."""
    spot_id = trip.spot_ids(2)[0]
    original = next(spot for spot in trip.day(2)["spots"] if spot["id"] == spot_id)
    trip.client.patch(
        f"{trip.base}/spots/{spot_id}",
        headers=trip.headers,
        json={"version": original["version"], "tip": "팁만 바꾼다"},
    )
    fresh = next(spot for spot in trip.day(2)["spots"] if spot["id"] == spot_id)
    assert fresh["tip"] == "팁만 바꾼다"
    for field in ("name", "name_original", "hours_text", "description", "recommendation", "lat", "lng"):
        assert fresh[field] == original[field]


def test_dwell_minutes_source_switches_between_explicit_and_band(trip: TripFixture) -> None:
    """AC-007 파생 (설계서 §6.8 · O6): 명시값이 있으면 `explicit`, 없으면 `time_band`."""
    spot = trip.day(1)["spots"][0]
    assert spot["dwell_source"] == "time_band"

    updated = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": spot["version"], "dwell_minutes": 40},
    ).json()
    assert updated["dwell_minutes"] == 40
    assert updated["dwell_source"] == "explicit"

    reverted = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": updated["version"], "dwell_minutes": None},
    ).json()
    assert reverted["dwell_source"] == "time_band"


def test_changing_time_label_moves_the_anchor(trip: TripFixture) -> None:
    """AC-007 파생: 라벨이 `HH:MM` 이 되면 `fixed_start_local` 앵커도 함께 생긴다.

    둘이 갈라지면 충돌 검사(REQ-013)가 화면과 다른 것을 본다.
    """
    spot = trip.day(3)["spots"][0]
    updated = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": spot["version"], "time_label": "09:30"},
    ).json()
    assert updated["fixed_start_local"] == "09:30"

    back = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        json={"version": updated["version"], "time_label": "오전"},
    ).json()
    assert back["fixed_start_local"] is None


def test_delete_removes_spot_and_shrinks_progress_denominator(trip: TripFixture) -> None:
    """AC-007: 삭제한 스팟은 목록에서 사라지고 진행률 분모가 1 줄어든다."""
    assert trip.state().json()["progress"]["total"] == 27
    spot_id = trip.spot_ids(1)[2]

    response = trip.client.delete(f"{trip.base}/spots/{spot_id}", headers=trip.headers)
    assert response.status_code == 204
    assert response.content == b""

    document = trip.state().json()
    assert document["progress"]["total"] == 26
    assert spot_id not in trip.spot_ids(1)


def test_delete_renumbers_the_remaining_spots(trip: TripFixture) -> None:
    """AC-008 일부: 삭제 뒤 남은 순서에 중복도 빈 자리도 없다."""
    before = trip.spot_ids(1)
    trip.client.delete(f"{trip.base}/spots/{before[1]}", headers=trip.headers)
    day = trip.day(1)
    assert [spot["position"] for spot in day["spots"]] == list(range(len(before) - 1))
    assert [spot["id"] for spot in day["spots"]] == [before[0], *before[2:]]


def test_deleting_a_done_spot_keeps_progress_consistent(trip: TripFixture) -> None:
    """AC-007 · AC-011: 완료 체크된 스팟을 지우면 분자·분모가 함께 줄어든다.

    `visit` 의 `ON DELETE CASCADE` 가 돌지 않으면 분자가 남아 진행률이 100% 를 넘는다 —
    그리고 그건 `PRAGMA foreign_keys` 를 안 켰을 때 **조용히** 일어난다(§12 F5).
    """
    spot_id = trip.spot_ids(1)[0]
    trip.client.put(f"{trip.base}/spots/{spot_id}/done", headers=trip.headers, json={"done": True})
    assert trip.state().json()["progress"] == {"done": 1, "total": 27, "percent": 4}

    trip.client.delete(f"{trip.base}/spots/{spot_id}", headers=trip.headers)
    assert trip.state().json()["progress"] == {"done": 0, "total": 26, "percent": 0}


def test_unknown_spot_and_foreign_spot_are_both_404(trip: TripFixture, client: Any) -> None:
    """AC-007 경계: 없는 스팟과 **다른 여행의 스팟**을 구분하지 않는다."""
    other = client.post("/api/trips", json={}).json()
    other_headers = {"X-Participant-Token": other["participant_token"]}
    other_spot = client.get(f"/api/trips/{other['trip']['id']}/state", headers=other_headers).json()
    foreign_id = other_spot["days"][0]["spots"][0]["id"]

    assert trip.client.delete(f"{trip.base}/spots/없는스팟", headers=trip.headers).status_code == 404
    assert trip.client.delete(f"{trip.base}/spots/{foreign_id}", headers=trip.headers).status_code == 404
    patch = trip.client.patch(f"{trip.base}/spots/{foreign_id}", headers=trip.headers, json={"version": 1})
    assert patch.status_code == 404
