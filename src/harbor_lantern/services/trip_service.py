"""여행 생성 · 참여 · 참가자 토큰 — DSN-06 · DSN-07 · DSN-08 (설계서 §6.17).

**참여 처리의 순서가 곧 보안 속성이다**(§6.17):

1. 레이트 리밋 검사 — 코드 유효성을 **보기 전에** 막는다(NFR-006).
2. 시도 기록 — 성공·실패 무관.
3. 표시명 검증 — 422 는 **이것 하나뿐**이다.
4. 초대코드 — 형식 오류와 없는 코드가 **완전히 같은 404 본문**이다(AC-040 · §12 F7).
   여기서 422 를 쓰면 "형식은 맞는데 없는 코드"와 구별돼 열거 공격의 신호가 된다.
5. 같은 여행에 같은 표시명이면 409.
6. 토큰 발급 — DB 에는 `sha256(원문)` 만 남는다(A10). 원문은 이 응답에서 한 번만 나간다.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import unicodedata
import uuid
from typing import Any

from harbor_lantern.api.errors import ConflictError, NotFoundError, UnprocessableError
from harbor_lantern.clock import Clock
from harbor_lantern.config import Settings
from harbor_lantern.domain.invite import generate_code, normalize_code
from harbor_lantern.domain.util import format_iso_utc
from harbor_lantern.services.plan_service import participant_payload, trip_payload
from harbor_lantern.storage import repo_trips
from harbor_lantern.storage.db import transaction
from harbor_lantern.storage.seed import install_seed

__all__ = [
    "INVITE_NOT_FOUND_MESSAGE",
    "create_trip",
    "hash_token",
    "issue_token",
    "join_trip",
    "resolve_participant",
]

INVITE_NOT_FOUND_MESSAGE = "초대코드를 찾을 수 없습니다."
INVITE_NOT_FOUND_CODE = "invite_not_found"
_MAX_CODE_ATTEMPTS = 5


def hash_token(token: str) -> str:
    """저장·조회는 해시로만 한다. 원문을 DB 에 넣으면 백업 하나가 곧 전 여행의 열쇠다."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def create_trip(
    conn: sqlite3.Connection,
    clock: Clock,
    settings: Settings,
    *,
    name: str,
    start_date: str,
    organizer_display_name: str,
) -> dict[str, Any]:
    """여행 1건 + 4일 + 27스팟 + 개설자 (REQ-001 · REQ-002 · AC-001 · AC-002 · AC-003)."""
    display_name = _validate_display_name(organizer_display_name, settings)
    now = format_iso_utc(clock.now_utc())
    trip_id = uuid.uuid4().hex
    participant_id = uuid.uuid4().hex
    token, token_hash = issue_token()

    with transaction(conn):
        _insert_trip_with_unique_code(
            conn, trip_id=trip_id, name=name, start_date=start_date, created_at=now
        )
        repo_trips.insert_participant(
            conn,
            participant_id=participant_id,
            trip_id=trip_id,
            display_name=display_name,
            token_hash=token_hash,
            is_organizer=True,
            joined_at=now,
        )
        install_seed(conn, trip_id=trip_id, start_date=start_date, created_at=now)

    trip = repo_trips.get_trip(conn, trip_id)
    participant = repo_trips.get_participant(conn, participant_id)
    if trip is None or participant is None:  # pragma: no cover - 방금 넣었다
        raise RuntimeError("여행 생성 직후 조회에 실패했다")
    return {
        "trip": trip_payload(trip),
        "participant": participant_payload(participant),
        "participant_token": token,
    }


def _insert_trip_with_unique_code(
    conn: sqlite3.Connection,
    *,
    trip_id: str,
    name: str,
    start_date: str,
    created_at: str,
) -> str:
    """`UNIQUE(invite_code)` 충돌은 60비트에서 사실상 안 나지만, 나면 조용히 500 이 된다.

    그래서 몇 번 다시 뽑는다 — 재시도 자체가 검사다.
    """
    last_error: sqlite3.IntegrityError | None = None
    for _ in range(_MAX_CODE_ATTEMPTS):
        invite_code = generate_code()
        try:
            repo_trips.insert_trip(
                conn,
                trip_id=trip_id,
                name=name,
                start_date=start_date,
                invite_code=invite_code,
                created_at=created_at,
            )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - 확률적으로 도달하지 않는다
            last_error = exc
            continue
        return invite_code
    raise RuntimeError(f"초대코드 생성에 실패했다: {last_error}")


def join_trip(
    conn: sqlite3.Connection,
    clock: Clock,
    settings: Settings,
    *,
    ip: str,
    invite_code: str,
    display_name: str,
) -> dict[str, Any]:
    """초대코드로 참여 (REQ-003 · AC-004 · AC-005 · AC-040).

    1)·2) 레이트 리밋 검사와 시도 기록은 **호출 전에** 끝나 있어야 한다
    (`api/ratelimit.enforce_join_rate_limit` — 라우트가 먼저 부른다). 그 순서가
    보안 속성이라 여기서 다시 하지 않는다.
    """
    del ip  # 리밋은 라우트가 이미 걸었다. 인자는 호출 순서를 문서화하려고 남긴다.
    now = format_iso_utc(clock.now_utc())

    # 3) 표시명 — 422 는 여기뿐이다.
    name = _validate_display_name(display_name, settings)

    # 4) 초대코드 — 형식 오류와 없는 코드의 응답이 완전히 같아야 한다(AC-040).
    normalized = normalize_code(invite_code)
    trip = None if normalized is None else repo_trips.get_trip_by_invite_code(conn, normalized)
    if trip is None:
        raise NotFoundError(INVITE_NOT_FOUND_MESSAGE, code=INVITE_NOT_FOUND_CODE)

    trip_id = str(trip["id"])
    if repo_trips.display_name_taken(conn, trip_id, name):
        raise ConflictError("같은 이름이 이미 있습니다. 다른 이름을 쓰세요.", code="display_name_taken")

    participant_id = uuid.uuid4().hex
    token, token_hash = issue_token()
    try:
        with transaction(conn):
            repo_trips.insert_participant(
                conn,
                participant_id=participant_id,
                trip_id=trip_id,
                display_name=name,
                token_hash=token_hash,
                is_organizer=False,
                joined_at=now,
            )
    except sqlite3.IntegrityError as exc:  # 같은 이름이 동시에 들어온 경우
        raise ConflictError("같은 이름이 이미 있습니다. 다른 이름을 쓰세요.", code="display_name_taken") from exc

    participant = repo_trips.get_participant(conn, participant_id)
    if participant is None:  # pragma: no cover
        raise RuntimeError("참가자 생성 직후 조회에 실패했다")
    return {
        "trip_id": trip_id,
        "participant": participant_payload(participant),
        "participant_token": token,
    }


def resolve_participant(conn: sqlite3.Connection, trip_id: str, token: str | None) -> sqlite3.Row:
    """`X-Participant-Token` → 참가자. 실패는 전부 **404** 다(403 이 아니다 — §6.17).

    토큰이 없거나, 틀렸거나, 다른 여행의 것이면 결과가 같아야 한다. 셋을 구분해 주는
    순간 "그 여행은 있다"가 새어 나간다.
    """
    if not token:
        raise NotFoundError()
    participant = repo_trips.get_participant_by_token_hash(conn, hash_token(token))
    if participant is None or str(participant["trip_id"]) != trip_id:
        raise NotFoundError()
    return participant


# ── 내부 ──────────────────────────────────────────────────────────────────
def _validate_display_name(raw: str, settings: Settings) -> str:
    name = unicodedata.normalize("NFKC", raw or "").strip()
    if not name or len(name) > settings.max_display_name_len:
        raise UnprocessableError(
            f"표시명은 1~{settings.max_display_name_len}자여야 합니다.",
            code="invalid_display_name",
        )
    return name
