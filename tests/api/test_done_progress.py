"""방문 완료 체크와 진행률 — AC-010 · AC-011 · AC-030 (REQ-006).

참조 HTML 은 `localStorage` 에 체크를 담았다. 그건 **혼자만 아는 체크**다 — REQ-006 이
바꾼 것이 이것이고, 그래서 "B 의 화면에도 보이는가"가 이 파일의 핵심 단언이다.
"""

from __future__ import annotations

from tests.api.conftest import Actor, TripFixture


def _done(trip: TripFixture, spot_id: str, done: bool, actor: Actor | None = None):
    who = actor or trip.owner
    return trip.client.put(f"{trip.base}/spots/{spot_id}/done", headers=who.headers, json={"done": done})


def test_check_records_who_and_when(trip: TripFixture) -> None:
    """AC-010: 체크하면 서버에 저장되고 응답에 **체크한 참가자 표시명과 시각**이 실린다."""
    spot_id = trip.spot_ids(1)[0]
    response = _done(trip, spot_id, True)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["spot_id"] == spot_id
    assert body["done"]["is_done"] is True
    assert body["done"]["by_participant_id"] == trip.owner.participant_id
    assert body["done"]["by_display_name"] == "개설자"
    # 고정 시계 = 2026-10-05 02:00 UTC (10:00 HKT). 저장은 UTC 다(NFR-013).
    assert body["done"]["at"] == "2026-10-05T02:00:00Z"
    assert body["progress"] == {"done": 1, "total": 27, "percent": 4}


def test_uncheck_returns_to_incomplete(trip: TripFixture) -> None:
    """AC-010: 다시 해제하면 미완료로 돌아간다."""
    spot_id = trip.spot_ids(1)[0]
    _done(trip, spot_id, True)
    body = _done(trip, spot_id, False).json()

    assert body["done"] == {"is_done": False, "by_participant_id": None, "by_display_name": None, "at": None}
    assert body["progress"]["done"] == 0


def test_check_is_idempotent(trip: TripFixture) -> None:
    """AC-010 경계: 같은 값을 두 번 보내도 결과가 같다(멱등) — 재시도가 안전하다."""
    spot_id = trip.spot_ids(2)[0]
    first = _done(trip, spot_id, True).json()
    second = _done(trip, spot_id, True).json()
    assert first["done"] == second["done"]
    assert second["progress"]["done"] == 1

    _done(trip, spot_id, False)
    assert _done(trip, spot_id, False).json()["progress"]["done"] == 0


def test_check_by_one_participant_is_visible_to_the_other(trip: TripFixture, guest: Actor) -> None:
    """AC-011: A 가 체크한 스팟이 B 의 조회에도 완료로 나타나고 진행률이 **동일**하다."""
    spot_id = trip.spot_ids(1)[0]
    _done(trip, spot_id, True, guest)

    owner_view = trip.state().json()
    guest_view = trip.state(guest).json()
    assert owner_view["progress"] == guest_view["progress"] == {"done": 1, "total": 27, "percent": 4}

    spot = next(item for item in owner_view["days"][0]["spots"] if item["id"] == spot_id)
    assert spot["done"]["is_done"] is True
    assert spot["done"]["by_participant_id"] == guest.participant_id
    assert spot["done"]["by_display_name"] == "동행"


def test_progress_percent_rounds_half_up(trip: TripFixture) -> None:
    """AC-030 · §12 F4: `3/24 = 12.5%` 는 **13** 이다.

    파이썬 내장 `round()` 는 은행가 반올림이라 12 를 낸다 — 참조 HTML 의 `Math.round`
    와 갈리고, 27개 중 **특정 개수에서만** 다르다. 조용히 틀리는 종류다.
    """
    for spot_id in trip.spot_ids(4)[:3]:
        assert trip.client.delete(f"{trip.base}/spots/{spot_id}", headers=trip.headers).status_code == 204
    assert trip.state().json()["progress"]["total"] == 24

    for spot_id in trip.spot_ids(1)[:3]:
        _done(trip, spot_id, True)
    assert trip.state().json()["progress"] == {"done": 3, "total": 24, "percent": 13}


def test_ac030_empty_trip_divides_by_zero_without_raising(trip: TripFixture) -> None:
    """AC-030 후단: **전체수가 0이면 0%** 이고 예외가 발생하지 않는다.

    Phase 4 의 AC 대조에서 이 절이 비어 있었다 — 구현(`plan_service.py` 의 `total <= 0`
    분기)은 있는데 그 분기를 밟는 테스트가 없었다. 가드를 지워도 27스팟이 남아 있는 한
    다른 테스트는 전부 통과하므로, **스팟을 다 지운 사람만** `ZeroDivisionError` 를 만난다.
    """
    for day_index in (1, 2, 3, 4):
        for spot_id in trip.spot_ids(day_index):
            assert trip.client.delete(f"{trip.base}/spots/{spot_id}", headers=trip.headers).status_code == 204

    document = trip.state()
    assert document.status_code == 200, document.text
    assert document.json()["progress"] == {"done": 0, "total": 0, "percent": 0}
    assert [len(day["spots"]) for day in document.json()["days"]] == [0, 0, 0, 0]
    assert document.json()["warnings"] == [] and document.json()["conflicts"] == []


def test_ac024_unparsable_hours_reach_the_api_as_unknown_and_warn_nothing(trip: TripFixture) -> None:
    """AC-024(API 경계): 파싱 불가 영업시간은 응답에 `hours.status == "unknown"` 으로 실리고
    **경고를 만들지 않는다**.

    도메인 쪽(`tests/domain/test_warn.py`)이 "unknown 은 경고하지 않는다"를 덮지만,
    AC-024 가 요구한 "응답에 '영업시간 미상' 표시 플래그가 포함된다"는 API 표면의 약속이다.
    플래그가 응답에서 빠지면 화면은 **읽지 못한 영업시간을 그냥 비워 두고**, 사용자는
    그것을 "영업시간 정보가 없는 곳"이 아니라 "24시간 여는 곳"으로 읽는다.
    """
    spot = trip.day(1)["spots"][0]
    patched = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=trip.headers,
        # 설계서 §6.9 가 의도적으로 unknown 으로 두는 문자열 (주체가 둘이다).
        json={"version": spot["version"], "hours_text": "단지 상시 · 상점 대략 11:00–20:00"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["hours"]["status"] == "unknown"
    assert patched.json()["hours"]["open_local"] is None
    assert patched.json()["hours"]["close_local"] is None

    document = trip.state().json()
    fresh = next(item for day in document["days"] for item in day["spots"] if item["id"] == spot["id"])
    assert fresh["hours"]["status"] == "unknown", "응답에 '영업시간 미상' 플래그가 없다 (AC-024)"
    assert fresh["hours_text"] == "단지 상시 · 상점 대략 11:00–20:00", "원문은 그대로 보존된다(A1)"
    assert not [w for w in document["warnings"] if w["spot_id"] == spot["id"]], (
        "unknown 인데 경고를 만들었다 — 거짓 경고 하나가 진짜 경고 전부를 무시하게 만든다 (R1 · AC-024)"
    )


def test_done_bumps_revision(trip: TripFixture) -> None:
    """AC-035: 완료 체크도 변경이므로 리비전이 오른다."""
    before = trip.revision()
    body = _done(trip, trip.spot_ids(1)[0], True).json()
    assert body["revision"] == before + 1


def test_done_on_unknown_spot_is_404(trip: TripFixture) -> None:
    """AC-010 경계: 없는 스팟은 404 다."""
    assert _done(trip, "없는스팟", True).status_code == 404


def test_done_requires_a_participant_token(trip: TripFixture) -> None:
    """AC-011 경계: 토큰이 없으면 404 다(403 이 아니다 — 존재를 알려 주지 않는다)."""
    response = trip.client.put(f"{trip.base}/spots/{trip.spot_ids(1)[0]}/done", json={"done": True})
    assert response.status_code == 404
