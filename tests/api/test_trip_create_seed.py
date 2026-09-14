"""여행 생성과 27스팟 시드 주입 — AC-001 · AC-002 · AC-003 (REQ-001 · REQ-002).

시드가 하나만 조용히 빠져도 그 뒤의 모든 계산(이동시간·경고·정산 분모)이 조용히 틀린다.
그래서 개수만 세지 않고 **시드 파일의 값과 API 응답을 한 건씩 대조한다.**
"""

from __future__ import annotations

from typing import Any

from tests.api.conftest import TripFixture

EXPECTED_DAY_COUNTS = [6, 9, 8, 4]


def test_create_returns_four_days_and_27_spots(trip: TripFixture) -> None:
    """AC-001: 4개 일자·총 27스팟, 일자별 6·9·8·4."""
    days = trip.state().json()["days"]
    assert [day["day_index"] for day in days] == [1, 2, 3, 4]
    assert [len(day["spots"]) for day in days] == EXPECTED_DAY_COUNTS
    assert sum(len(day["spots"]) for day in days) == 27


def test_seeded_spot_fields_match_seed_file(trip: TripFixture, seed_document: dict[str, Any]) -> None:
    """AC-001: 이름·원어명·시간대·팁·영업시간·휴무·설명·추천·위경도가 시드와 **일치**한다."""
    days = trip.state().json()["days"]
    for seed_day, day in zip(seed_document["days"], days, strict=True):
        assert day["title"] == seed_day["title"]
        assert day["area"] == seed_day["area"]
        assert day["color"] == seed_day["color"]
        assert day["start_local"] == seed_day["start_local"]
        for seed_spot, spot in zip(seed_day["spots"], day["spots"], strict=True):
            for field in (
                "time_label",
                "name",
                "name_original",
                "tip",
                "hours_text",
                "closed_text",
                "description",
                "recommendation",
            ):
                assert spot[field] == seed_spot[field], f"{field} 가 시드와 다르다: {spot['name']}"
            assert spot["lat"] == seed_spot["lat"]
            assert spot["lng"] == seed_spot["lng"]


def test_spot_positions_are_dense_from_zero(trip: TripFixture) -> None:
    """AC-001: 일자마다 순서값이 0부터 연속이고 중복이 없다."""
    for day in trip.state().json()["days"]:
        positions = [spot["position"] for spot in day["spots"]]
        assert positions == list(range(len(positions)))


def test_fixed_time_label_becomes_anchor(trip: TripFixture) -> None:
    """AC-001 파생: `HH:MM` 시간대 라벨은 `fixed_start_local` 앵커가 된다(설계서 §5.3).

    앵커가 유도되지 않으면 REQ-013 의 충돌 검사가 아무것도 못 잡는다 — 조용히.
    """
    spots = [spot for day in trip.state().json()["days"] for spot in day["spots"]]
    anchored = [spot for spot in spots if spot["fixed_start_local"]]
    assert [spot["time_label"] for spot in anchored] == ["20:00"]
    assert anchored[0]["fixed_start_local"] == "20:00"
    assert all(spot["fixed_start_local"] is None for spot in spots if ":" not in spot["time_label"])


def test_default_start_date_spans_four_consecutive_days(trip: TripFixture) -> None:
    """AC-002: 시작일 미지정이면 Day 1 = 2026-10-05, Day 4 = 2026-10-08 이다."""
    days = trip.state().json()["days"]
    assert [day["date"] for day in days] == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    # 요일은 날짜에서만 나온다 — **현재 시각과 무관**하다(§12 F1).
    assert [day["weekday"] for day in days] == [0, 1, 2, 3]


def test_explicit_start_date_shifts_all_days(client: Any) -> None:
    """AC-002: 시작일을 지정하면 4일이 그 날짜부터 연속 배정된다."""
    created = client.post("/api/trips", json={"start_date": "2027-01-30"})
    assert created.status_code == 201, created.text
    body = created.json()
    headers = {"X-Participant-Token": body["participant_token"]}
    days = client.get(f"/api/trips/{body['trip']['id']}/state", headers=headers).json()["days"]
    assert [day["date"] for day in days] == ["2027-01-30", "2027-01-31", "2027-02-01", "2027-02-02"]


def test_invalid_start_date_is_rejected(client: Any) -> None:
    """AC-002 경계: 날짜가 아닌 문자열은 422 이고 여행이 만들어지지 않는다."""
    response = client.post("/api/trips", json={"start_date": "2026-13-99"})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_invite_code_is_stable_and_unique(client: Any, trip: TripFixture) -> None:
    """AC-003: 다시 조회해도 같은 코드, 다른 여행은 다른 코드."""
    again = client.get(trip.base, headers=trip.headers)
    assert again.status_code == 200, again.text
    assert again.json()["invite_code"] == trip.invite_code
    assert again.json()["invite_code_display"] == "-".join(
        [trip.invite_code[0:4], trip.invite_code[4:8], trip.invite_code[8:12]]
    )

    other = client.post("/api/trips", json={}).json()
    assert other["trip"]["invite_code"] != trip.invite_code


def test_invite_code_shape_is_crockford_base32_twelve(trip: TripFixture) -> None:
    """AC-003 · NFR-005: 정규형 12자, I·L·O·U 가 없다(60.0비트)."""
    assert len(trip.invite_code) == 12
    assert set(trip.invite_code) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_organizer_is_flagged_and_progress_starts_at_zero(trip: TripFixture) -> None:
    """AC-001 · AC-030: 개설자 표시, 진행률 0/27 = 0% (0 으로 나누지 않는다)."""
    document = trip.state().json()
    assert document["participants"][0]["is_organizer"] is True
    assert document["progress"] == {"done": 0, "total": 27, "percent": 0}
    assert document["expenses_summary"] == {"count": 0, "total_minor": 0, "currency": "HKD"}
