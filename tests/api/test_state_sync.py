"""리비전 동기화와 `/state` 의 시각 독립성 — AC-035 · AC-036 (REQ-016 · §12 F1).

**이 파일의 첫 번째 테스트가 가장 중요하다.** ETag 를 `trip.revision` 으로 만드는데
응답 본문이 서버 현재 시각에 의존하면, 304 를 받은 클라이언트는 바뀐 내용을 영영 못 보고
**에러 로그도 안 남는다.** 그래서 시계를 두 번 다르게 놓고 **같은 본문**인지 본다.
"""

from __future__ import annotations

from harbor_lantern.clock import FixedClock
from tests.api.conftest import Actor, TripFixture


def test_state_body_does_not_depend_on_the_current_time(trip: TripFixture, fixed_clock: FixedClock) -> None:
    """§12 F1 · AC-036: 시계를 9시간 밀어도 `/state` 본문과 ETag 가 **완전히 같다**.

    현지 시계·"지금 열려 있나"는 클라이언트가 계산한다 — 서버가 그것을 본문에 넣는 순간
    ETag 가 거짓말을 한다.
    """
    morning = trip.state()
    fixed_clock.advance(9 * 3600)  # 10:00 HKT → 19:00 HKT (영업시간 판정이 뒤집힐 시각)
    evening = trip.state()

    assert morning.status_code == evening.status_code == 200
    assert morning.json() == evening.json()
    assert morning.headers["ETag"] == evening.headers["ETag"]
    assert morning.content == evening.content


def test_etag_matches_revision_and_repeats_without_change(trip: TripFixture) -> None:
    """AC-035: 리비전은 단조 증가하고, **아무 변경이 없으면 동일**하다."""
    first = trip.state()
    assert first.headers["ETag"] == '"1"'
    assert first.json()["trip"]["revision"] == 1

    second = trip.state()
    assert second.headers["ETag"] == '"1"'
    assert second.json()["trip"]["revision"] == 1


def test_unchanged_poll_returns_304_without_body(trip: TripFixture) -> None:
    """AC-036: 보유한 리비전과 같으면 **304 · 본문 없음**이다."""
    first = trip.state()
    again = trip.state(headers={"If-None-Match": first.headers["ETag"]})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == first.headers["ETag"]


def test_weak_and_wildcard_etags_are_accepted(trip: TripFixture) -> None:
    """AC-036 경계: `W/"1"` 와 `*` 도 같은 것으로 본다(HTTP 규약).

    프록시가 `W/` 를 붙여 보내는 일이 실제로 있다. 그때 매번 200 을 주면 폴링이
    전체 상태를 계속 실어 나르는데, **아무도 그것을 눈치채지 못한다.**
    """
    etag = trip.state().headers["ETag"]
    assert trip.state(headers={"If-None-Match": f"W/{etag}"}).status_code == 304
    assert trip.state(headers={"If-None-Match": "*"}).status_code == 304


def test_edit_bumps_revision_and_stale_poll_gets_the_change(trip: TripFixture, guest: Actor) -> None:
    """AC-035 · AC-036: B 가 스팟을 고치면 A 의 낡은 리비전 폴링이 **변경된 스팟**을 받는다."""
    snapshot = trip.state()
    stale_etag = snapshot.headers["ETag"]
    spot = snapshot.json()["days"][0]["spots"][0]

    edited = trip.client.patch(
        f"{trip.base}/spots/{spot['id']}",
        headers=guest.headers,
        json={"version": spot["version"], "name": "B 가 고친 이름"},
    )
    assert edited.status_code == 200, edited.text

    polled = trip.state(headers={"If-None-Match": stale_etag})
    assert polled.status_code == 200
    assert polled.headers["ETag"] != stale_etag
    changed = polled.json()["days"][0]["spots"][0]
    assert changed["name"] == "B 가 고친 이름"
    assert polled.json()["trip"]["revision"] == snapshot.json()["trip"]["revision"] + 1

    # 새 ETag 로 다시 물으면 조용해진다 — 폴링이 무한히 200 을 받지 않는다.
    assert trip.state(headers={"If-None-Match": polled.headers["ETag"]}).status_code == 304


def test_every_kind_of_write_bumps_the_revision(trip: TripFixture) -> None:
    """AC-035: 생성·수정·삭제·재정렬·완료체크·경비가 전부 리비전을 올린다."""
    revisions = [trip.revision()]

    created = trip.client.post(
        f"{trip.base}/days/1/spots",
        headers=trip.headers,
        json={"name": "추가", "lat": 22.3, "lng": 114.2},
    ).json()
    revisions.append(trip.revision())

    trip.client.patch(
        f"{trip.base}/spots/{created['id']}",
        headers=trip.headers,
        json={"version": created["version"], "tip": "수정"},
    )
    revisions.append(trip.revision())

    trip.client.put(
        f"{trip.base}/days/1/order",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "spot_ids": list(reversed(trip.spot_ids(1)))},
    )
    revisions.append(trip.revision())

    trip.client.put(f"{trip.base}/spots/{created['id']}/done", headers=trip.headers, json={"done": True})
    revisions.append(trip.revision())

    trip.client.post(
        f"{trip.base}/expenses",
        headers=trip.headers,
        json={
            "payer_id": trip.owner.participant_id,
            "amount_minor": 100,
            "share_participant_ids": [trip.owner.participant_id],
        },
    )
    revisions.append(trip.revision())

    trip.client.delete(f"{trip.base}/spots/{created['id']}", headers=trip.headers)
    revisions.append(trip.revision())

    assert revisions == sorted(set(revisions)), f"리비전이 단조 증가하지 않는다: {revisions}"
    assert revisions[-1] == revisions[0] + 6


def test_read_only_endpoints_do_not_bump_the_revision(trip: TripFixture) -> None:
    """AC-035 경계: 조회와 **동선 최적화 제안**은 쓰기가 아니다(§6.16)."""
    before = trip.revision()
    trip.client.get(f"{trip.base}/expenses", headers=trip.headers)
    trip.client.get(f"{trip.base}/settlement", headers=trip.headers)
    trip.client.post(f"{trip.base}/days/2/optimize", headers=trip.headers)
    assert trip.revision() == before


def test_state_carries_schedule_legs_totals_and_warning_lists(trip: TripFixture) -> None:
    """AC-036 파생: 폴링 한 번으로 화면을 다 그릴 수 있어야 한다(전체 상태 응답)."""
    document = trip.state().json()
    assert set(document) == {
        "trip",
        "participants",
        "progress",
        "days",
        "warnings",
        "conflicts",
        "expenses_summary",
    }

    day = document["days"][0]
    assert day["start_local"] == "14:00"
    assert set(day["totals"]) == {"distance_m", "travel_minutes", "dwell_minutes"}
    assert day["totals"]["distance_m"] > 0

    first, *_rest, last = day["spots"]
    assert first["schedule"]["eta_local"] == "14:00"
    assert first["schedule"]["eta_day_offset"] == 0
    assert first["leg_to_next"]["to_spot_id"] == day["spots"][1]["id"]
    assert first["leg_to_next"]["estimated"] is True
    assert first["leg_to_next"]["mode"] in {"walk", "transit"}
    assert last["leg_to_next"] is None

    assert isinstance(document["warnings"], list)
    assert isinstance(document["conflicts"], list)


def test_reordering_shifts_the_timeline(trip: TripFixture) -> None:
    """AC-021(통합): 순서가 바뀌면 도착 예상시각이 **전부 재계산**된다."""
    before = {spot["id"]: spot["schedule"]["eta_local"] for spot in trip.day(2)["spots"]}
    ids = trip.spot_ids(2)
    trip.client.put(
        f"{trip.base}/days/2/order",
        headers=trip.headers,
        json={"expected_revision": trip.revision(), "spot_ids": list(reversed(ids))},
    )
    after = {spot["id"]: spot["schedule"]["eta_local"] for spot in trip.day(2)["spots"]}

    assert before != after
    assert after[ids[-1]] == "09:00"  # 이제 이 스팟이 하루의 첫 칸이다


def test_state_requires_a_token_of_the_same_trip(trip: TripFixture, client) -> None:
    """AC-036 경계: 토큰 없음 · 남의 여행 토큰 둘 다 **404** 다(403 이 아니다)."""
    assert client.get(f"{trip.base}/state").status_code == 404

    other = client.post("/api/trips", json={}).json()
    foreign = {"X-Participant-Token": other["participant_token"]}
    assert client.get(f"{trip.base}/state", headers=foreign).status_code == 404
    assert client.get("/api/trips/없는여행/state", headers=trip.headers).status_code == 404
