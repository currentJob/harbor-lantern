"""초대코드 참여와 IP 레이트 리밋 — AC-004 · AC-005 · AC-040 (REQ-003 · NFR-006).

**이 파일이 지키는 것은 기능이 아니라 보안 속성이다.** 형식이 틀린 코드에 422 를 주면
기능은 멀쩡히 동작하고 아무것도 터지지 않는다 — 열거 신호만 조용히 생긴다(§12 F7).
그래서 상태코드가 아니라 **응답 본문이 바이트 단위로 같은지**를 본다.
"""

from __future__ import annotations

from typing import Any

from harbor_lantern.clock import FixedClock
from tests.api.conftest import TripFixture

MALFORMED = "짧다"  # 정규화하면 12자가 안 된다 → normalize_code 가 None
NONEXISTENT = "ZZZZZZZZZZZZ"  # 형식은 완전히 정상, 다만 그런 여행이 없다


def _join(client: Any, code: str, name: str = "누군가") -> Any:
    return client.post("/api/join", json={"invite_code": code, "display_name": name})


def test_join_with_valid_code_issues_token(trip: TripFixture) -> None:
    """AC-005: 유효한 초대코드로 참여하면 참가자 토큰이 발급된다."""
    response = _join(trip.client, trip.invite_code, "동행")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trip_id"] == trip.trip_id
    assert body["participant"]["is_organizer"] is False
    assert len(body["participant_token"]) >= 32


def test_guest_sees_the_same_spot_set_as_organizer(trip: TripFixture, guest: Any) -> None:
    """AC-005: 참여자 토큰으로 조회한 일정이 개설자의 것과 **동일한 스팟 집합**이다."""
    owner_ids = [spot["id"] for day in trip.state().json()["days"] for spot in day["spots"]]
    guest_ids = [spot["id"] for day in trip.state(guest).json()["days"] for spot in day["spots"]]
    assert owner_ids == guest_ids
    assert len(owner_ids) == 27


def test_invite_code_is_normalized(trip: TripFixture) -> None:
    """AC-004 파생: 대소문자·하이픈·공백 무관하게 같은 코드로 본다(§6.4)."""
    pretty = f" {trip.invite_code[:4]}-{trip.invite_code[4:8]}-{trip.invite_code[8:]} ".lower()
    response = _join(trip.client, pretty, "느슨하게친구")
    assert response.status_code == 200, response.text


def test_unknown_code_returns_404_and_creates_nobody(trip: TripFixture) -> None:
    """AC-004: 존재하지 않는 코드는 404 이고 참가자가 생성되지 않는다."""
    before = len(trip.state().json()["participants"])
    response = _join(trip.client, NONEXISTENT)
    assert response.status_code == 404
    assert response.json() == {"error": "invite_not_found", "message": "초대코드를 찾을 수 없습니다."}
    assert len(trip.state().json()["participants"]) == before


def test_malformed_and_unknown_codes_are_indistinguishable(trip: TripFixture) -> None:
    """AC-040 · §12 F7: 형식 오류와 없는 코드의 **상태코드와 본문이 완전히 같다**.

    여기서 422 를 쓰면 "형식은 맞는데 없는 코드"와 구별돼 열거 공격의 신호가 된다.
    """
    malformed = _join(trip.client, MALFORMED)
    unknown = _join(trip.client, NONEXISTENT)
    assert malformed.status_code == unknown.status_code == 404
    assert malformed.json() == unknown.json()
    assert malformed.content == unknown.content


def test_empty_or_too_long_display_name_is_422(trip: TripFixture) -> None:
    """AC-004: 표시명이 비었거나 최대 길이를 넘으면 422. **422 는 표시명 문제에만** 쓴다."""
    empty = _join(trip.client, trip.invite_code, "   ")
    too_long = _join(trip.client, trip.invite_code, "가" * 25)
    assert empty.status_code == 422
    assert too_long.status_code == 422
    assert empty.json()["error"] == "invalid_display_name"


def test_duplicate_display_name_in_same_trip_is_409(trip: TripFixture, guest: Any) -> None:
    """AC-004 경계: 같은 여행에 같은 표시명은 409 (`UNIQUE(trip_id, display_name)`)."""
    response = _join(trip.client, trip.invite_code, "동행")
    assert response.status_code == 409
    assert response.json()["error"] == "display_name_taken"


def test_rate_limit_blocks_after_threshold(trip: TripFixture, settings: Any) -> None:
    """AC-040 · NFR-006: 같은 IP 의 시도가 임계(10회)를 넘으면 429 + `Retry-After`."""
    limit = settings.join_rate_limit_n
    for attempt in range(limit):
        assert _join(trip.client, NONEXISTENT).status_code == 404, f"{attempt + 1}번째 시도"

    blocked = _join(trip.client, NONEXISTENT)
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "rate_limited"
    retry_after = int(blocked.headers["Retry-After"])
    assert 1 <= retry_after <= settings.join_rate_limit_window_s + 1


def test_rate_limit_precedes_code_validation(trip: TripFixture, settings: Any) -> None:
    """AC-040 · §6.17: 리밋은 **코드 유효성보다 먼저** 걸린다.

    순서가 뒤집히면 공격자는 429 를 받기 전에 코드가 맞는지 아닌지를 이미 알게 된다.
    그래서 리밋에 걸린 뒤에는 **유효한 코드조차** 429 여야 한다.
    """
    for _ in range(settings.join_rate_limit_n):
        _join(trip.client, NONEXISTENT)
    blocked = _join(trip.client, trip.invite_code, "정상참가자")
    assert blocked.status_code == 429


def test_rate_limit_window_expires(trip: TripFixture, settings: Any, fixed_clock: FixedClock) -> None:
    """AC-040: 창(600초)이 지나면 다시 시도할 수 있다.

    `sleep` 대신 주입된 시계를 민다 — 그래야 스위트가 빠르고 결과가 기계 성능에 안 흔들린다.
    """
    for _ in range(settings.join_rate_limit_n):
        _join(trip.client, NONEXISTENT)
    assert _join(trip.client, NONEXISTENT).status_code == 429

    fixed_clock.advance(settings.join_rate_limit_window_s + 1)
    assert _join(trip.client, trip.invite_code, "나중에온사람").status_code == 200


def test_successful_join_also_counts_as_an_attempt(trip: TripFixture, settings: Any) -> None:
    """AC-040 경계: 성공한 시도도 센다 — 실패만 세면 유효한 코드로 열거를 계속할 수 있다."""
    for index in range(settings.join_rate_limit_n):
        assert _join(trip.client, trip.invite_code, f"참가자{index}").status_code == 200
    assert _join(trip.client, trip.invite_code, "한명더").status_code == 429
