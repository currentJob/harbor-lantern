"""`spot` · `visit` 리포지토리 — 재정렬 2단계 UPDATE 포함 (설계서 §6.11 · §12 F2).

**재정렬을 한 번에 쓰면 안 된다.** `UNIQUE(day_id, position)` 이 걸려 있어서 중간
상태에서 충돌한다 — 스팟이 2개일 때는 우연히 통과하고 3개 이상 특정 순열에서만
실패한다. 그래서 ① 음수 영역으로 대피시키고 ② 0..n-1 을 다시 부여한다.
두 단계는 **같은 트랜잭션 안에서** 일어나야 한다(호출부가 `transaction()` 으로 감싼다).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "assign_positions",
    "clear_visit",
    "count_spots",
    "count_visits",
    "delete_spot",
    "escape_positions",
    "get_spot",
    "get_spot_with_day",
    "get_visit",
    "insert_spot",
    "list_spot_ids_of_day",
    "list_spots_by_day",
    "list_spots_by_trip",
    "list_visits",
    "move_spot_between_days",
    "next_position",
    "renumber_day",
    "set_spot_day",
    "set_visit",
    "update_spot",
]

# `PATCH` 로 갱신할 수 있는 컬럼. 여기 없는 이름이 오면 그냥 무시하는 것이 아니라
# 호출부(스키마)가 이미 걸러 낸다 — 이 목록은 SQL 조립의 **화이트리스트**다.
UPDATABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "time_label",
        "fixed_start_local",
        "name",
        "name_original",
        "tip",
        "hours_text",
        "closed_text",
        "description",
        "recommendation",
        "lat",
        "lng",
        "dwell_minutes",
    }
)

# 대피 오프셋. 일자 간 이동에서 두 일자를 동시에 대피시킬 때 **서로 다른 구간**을
# 써야 한다 — 같은 오프셋을 쓰면 옮겨 온 스팟의 음수 position 이 대상 일자의 것과
# 겹쳐 UNIQUE 가 다시 터진다.
ESCAPE_OFFSET_PRIMARY = 1
ESCAPE_OFFSET_SECONDARY = 100_001


def insert_spot(
    conn: sqlite3.Connection,
    *,
    spot_id: str,
    trip_id: str,
    day_id: str,
    position: int,
    time_label: str,
    fixed_start_local: str | None,
    name: str,
    name_original: str,
    tip: str,
    hours_text: str,
    closed_text: str,
    description: str,
    recommendation: str,
    lat: float,
    lng: float,
    dwell_minutes: int | None,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        "INSERT INTO spot (id, trip_id, day_id, position, time_label, fixed_start_local, name,"
        " name_original, tip, hours_text, closed_text, description, recommendation, lat, lng,"
        " dwell_minutes, version, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (
            spot_id,
            trip_id,
            day_id,
            position,
            time_label,
            fixed_start_local,
            name,
            name_original,
            tip,
            hours_text,
            closed_text,
            description,
            recommendation,
            lat,
            lng,
            dwell_minutes,
            created_at,
            updated_at,
        ),
    )


def get_spot(conn: sqlite3.Connection, trip_id: str, spot_id: str) -> sqlite3.Row | None:
    """`trip_id` 를 함께 건다 — 다른 여행의 스팟 ID 를 주면 없는 것과 같아야 한다."""
    return conn.execute(
        "SELECT * FROM spot WHERE id = ? AND trip_id = ?",
        (spot_id, trip_id),
    ).fetchone()


def get_spot_with_day(conn: sqlite3.Connection, trip_id: str, spot_id: str) -> sqlite3.Row | None:
    """스팟 + 그 스팟이 속한 `day_index`. 단건 응답이 일자 번호를 필요로 한다(계약 `Spot`)."""
    return conn.execute(
        "SELECT spot.*, day.day_index AS day_index FROM spot"
        " JOIN day ON day.id = spot.day_id"
        " WHERE spot.id = ? AND spot.trip_id = ?",
        (spot_id, trip_id),
    ).fetchone()


def list_spots_by_day(conn: sqlite3.Connection, day_id: str) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM spot WHERE day_id = ? ORDER BY position", (day_id,)))


def list_spots_by_trip(conn: sqlite3.Connection, trip_id: str) -> list[sqlite3.Row]:
    """여행 전체를 일자·순서대로. `/state` 가 한 번에 읽는다(§8 — 쿼리 수를 늘리지 않는다)."""
    return list(
        conn.execute(
            "SELECT spot.*, day.day_index AS day_index FROM spot"
            " JOIN day ON day.id = spot.day_id"
            " WHERE spot.trip_id = ? ORDER BY day.day_index, spot.position",
            (trip_id,),
        )
    )


def list_spot_ids_of_day(conn: sqlite3.Connection, day_id: str) -> list[str]:
    return [str(row["id"]) for row in conn.execute("SELECT id FROM spot WHERE day_id = ? ORDER BY position", (day_id,))]


def next_position(conn: sqlite3.Connection, day_id: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(position), -1) AS m FROM spot WHERE day_id = ?", (day_id,)).fetchone()
    return int(row["m"]) + 1


def update_spot(
    conn: sqlite3.Connection,
    *,
    spot_id: str,
    expected_version: int,
    fields: Mapping[str, Any],
    updated_at: str,
) -> bool:
    """낙관적 잠금 갱신 (§6.18 · AC-047). 버전이 다르면 **아무것도 쓰지 않고** False.

    비교는 **SQL 의 `WHERE` 절에서** 한다. 파이썬에서 읽고 비교한 뒤 쓰면 그 사이가
    경합 창이다 — `rowcount` 가 유일하게 믿을 수 있는 신호다.
    """
    unknown = set(fields) - UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"갱신할 수 없는 컬럼: {sorted(unknown)}")
    assignments = ", ".join(f"{column} = ?" for column in fields)
    prefix = f"{assignments}, " if assignments else ""
    sql = f"UPDATE spot SET {prefix}version = version + 1, updated_at = ? WHERE id = ? AND version = ?"
    params = (*fields.values(), updated_at, spot_id, expected_version)
    return conn.execute(sql, params).rowcount == 1


def delete_spot(conn: sqlite3.Connection, spot_id: str) -> bool:
    return conn.execute("DELETE FROM spot WHERE id = ?", (spot_id,)).rowcount == 1


def escape_positions(conn: sqlite3.Connection, day_id: str, offset: int = ESCAPE_OFFSET_PRIMARY) -> None:
    """1단계 — 일자의 `position` 을 음수 영역으로 대피시킨다(§6.11).

    두 일자를 동시에 다룰 때는 오프셋을 다르게 준다(`ESCAPE_OFFSET_SECONDARY`).
    """
    conn.execute("UPDATE spot SET position = -(position + ?) WHERE day_id = ?", (offset, day_id))


def assign_positions(
    conn: sqlite3.Connection,
    day_id: str,
    ordered_ids: Sequence[str],
    updated_at: str,
) -> None:
    """2단계 — 요청 순서대로 0..n-1 을 부여한다. 한 건이라도 안 맞으면 예외(→ 롤백)."""
    for position, spot_id in enumerate(ordered_ids):
        changed = conn.execute(
            "UPDATE spot SET position = ?, updated_at = ? WHERE id = ? AND day_id = ?",
            (position, updated_at, spot_id, day_id),
        ).rowcount
        if changed != 1:
            raise LookupError(f"일자 {day_id} 에 없는 스팟: {spot_id}")


def renumber_day(
    conn: sqlite3.Connection,
    day_id: str,
    ordered_ids: Sequence[str],
    updated_at: str,
    *,
    offset: int = ESCAPE_OFFSET_PRIMARY,
) -> None:
    escape_positions(conn, day_id, offset)
    assign_positions(conn, day_id, ordered_ids, updated_at)


def set_spot_day(conn: sqlite3.Connection, spot_id: str, day_id: str) -> None:
    conn.execute("UPDATE spot SET day_id = ? WHERE id = ?", (day_id, spot_id))


def move_spot_between_days(
    conn: sqlite3.Connection,
    *,
    spot_id: str,
    from_day_id: str,
    to_day_id: str,
    from_order: Sequence[str],
    to_order: Sequence[str],
    updated_at: str,
) -> None:
    """일자 간 이동 (AC-009) — 양쪽 일자를 **같은 트랜잭션에서** 0..n-1 로 재부여한다.

    대피 오프셋을 두 일자에 다르게 주는 것이 요점이다. 같은 값을 쓰면 옮겨 온
    스팟의 음수 position 이 대상 일자의 대피값과 충돌한다.
    """
    escape_positions(conn, from_day_id, ESCAPE_OFFSET_PRIMARY)
    escape_positions(conn, to_day_id, ESCAPE_OFFSET_SECONDARY)
    set_spot_day(conn, spot_id, to_day_id)
    assign_positions(conn, to_day_id, to_order, updated_at)
    assign_positions(conn, from_day_id, from_order, updated_at)


# ── visit (완료 체크) ─────────────────────────────────────────────────────
def set_visit(
    conn: sqlite3.Connection,
    *,
    spot_id: str,
    trip_id: str,
    done_by: str,
    done_at: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO visit (spot_id, trip_id, done_by, done_at) VALUES (?, ?, ?, ?)",
        (spot_id, trip_id, done_by, done_at),
    )


def clear_visit(conn: sqlite3.Connection, spot_id: str) -> None:
    conn.execute("DELETE FROM visit WHERE spot_id = ?", (spot_id,))


def get_visit(conn: sqlite3.Connection, spot_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM visit WHERE spot_id = ?", (spot_id,)).fetchone()


def list_visits(conn: sqlite3.Connection, trip_id: str) -> dict[str, sqlite3.Row]:
    return {str(row["spot_id"]): row for row in conn.execute("SELECT * FROM visit WHERE trip_id = ?", (trip_id,))}


def count_spots(conn: sqlite3.Connection, trip_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM spot WHERE trip_id = ?", (trip_id,)).fetchone()
    return int(row["n"])


def count_visits(conn: sqlite3.Connection, trip_id: str) -> int:
    """분모는 `COUNT(spot)`, 분자는 `COUNT(visit)` 이다. 스팟을 지우면 `visit` 이
    CASCADE 로 함께 사라지므로 진행률이 자동으로 맞는다(§6.18 · AC-007)."""
    row = conn.execute("SELECT COUNT(*) AS n FROM visit WHERE trip_id = ?", (trip_id,)).fetchone()
    return int(row["n"])
