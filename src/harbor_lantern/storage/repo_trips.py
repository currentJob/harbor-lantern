"""`trip` · `participant` · `day` 리포지토리 (설계서 §5.2).

비즈니스 판단은 하지 않는다 — SQL 과 행(Row) 만 다룬다(§2.1).
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "bump_revision",
    "display_name_taken",
    "get_day",
    "get_participant",
    "get_participant_by_token_hash",
    "get_trip",
    "get_trip_by_invite_code",
    "insert_day",
    "insert_participant",
    "insert_trip",
    "list_days",
    "list_participants",
]


# ── trip ──────────────────────────────────────────────────────────────────
def insert_trip(
    conn: sqlite3.Connection,
    *,
    trip_id: str,
    name: str,
    start_date: str,
    invite_code: str,
    created_at: str,
    base_currency: str = "HKD",
) -> None:
    conn.execute(
        "INSERT INTO trip (id, name, start_date, base_currency, invite_code, revision, created_at)"
        " VALUES (?, ?, ?, ?, ?, 1, ?)",
        (trip_id, name, start_date, base_currency, invite_code, created_at),
    )


def get_trip(conn: sqlite3.Connection, trip_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM trip WHERE id = ?", (trip_id,)).fetchone()


def get_trip_by_invite_code(conn: sqlite3.Connection, invite_code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM trip WHERE invite_code = ?", (invite_code,)).fetchone()


def bump_revision(conn: sqlite3.Connection, trip_id: str) -> int:
    """모든 변경 트랜잭션이 부르는 곳 (DSN-21 · AC-035). 올라간 값을 돌려준다.

    `trip.revision` 은 **동기화용**이다. 리소스 낙관적 잠금(`spot.version`·
    `expense.version`)과 다른 것이다 — 섞어 쓰면 409 가 안 나거나 남발된다(§12 F3).
    """
    conn.execute("UPDATE trip SET revision = revision + 1 WHERE id = ?", (trip_id,))
    row = conn.execute("SELECT revision FROM trip WHERE id = ?", (trip_id,)).fetchone()
    if row is None:
        raise LookupError(f"여행이 없다: {trip_id}")
    return int(row["revision"])


# ── participant ───────────────────────────────────────────────────────────
def insert_participant(
    conn: sqlite3.Connection,
    *,
    participant_id: str,
    trip_id: str,
    display_name: str,
    token_hash: str,
    is_organizer: bool,
    joined_at: str,
) -> None:
    conn.execute(
        "INSERT INTO participant (id, trip_id, display_name, token_hash, is_organizer, joined_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (participant_id, trip_id, display_name, token_hash, 1 if is_organizer else 0, joined_at),
    )


def list_participants(conn: sqlite3.Connection, trip_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM participant WHERE trip_id = ? ORDER BY joined_at, id",
            (trip_id,),
        )
    )


def get_participant(conn: sqlite3.Connection, participant_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM participant WHERE id = ?", (participant_id,)).fetchone()


def get_participant_by_token_hash(conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
    """토큰 **원문은 저장하지 않는다**(A10). 조회는 sha256 해시로만 한다(§6.17)."""
    return conn.execute("SELECT * FROM participant WHERE token_hash = ?", (token_hash,)).fetchone()


def display_name_taken(conn: sqlite3.Connection, trip_id: str, display_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM participant WHERE trip_id = ? AND display_name = ?",
        (trip_id, display_name),
    ).fetchone()
    return row is not None


# ── day ───────────────────────────────────────────────────────────────────
def insert_day(
    conn: sqlite3.Connection,
    *,
    day_id: str,
    trip_id: str,
    day_index: int,
    date: str,
    title: str,
    area: str,
    color: str,
    start_local: str,
) -> None:
    conn.execute(
        "INSERT INTO day (id, trip_id, day_index, date, title, area, color, start_local)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (day_id, trip_id, day_index, date, title, area, color, start_local),
    )


def list_days(conn: sqlite3.Connection, trip_id: str) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM day WHERE trip_id = ? ORDER BY day_index", (trip_id,)))


def get_day(conn: sqlite3.Connection, trip_id: str, day_index: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM day WHERE trip_id = ? AND day_index = ?",
        (trip_id, day_index),
    ).fetchone()
