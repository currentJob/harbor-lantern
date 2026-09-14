"""`expense` · `expense_share` 리포지토리 (설계서 §6.20 · NFR-014).

금액은 전부 **정수 minor unit**(HKD cent)이다. 이 파일에 float 이 나타나면 안 된다.
분담액은 **기록 시점에 확정 저장**한다 — 나중에 참가자가 추가돼도 과거 지출의 분담이
흔들리지 않는다(재계산 드리프트가 원천적으로 없다).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

__all__ = [
    "delete_expense",
    "get_expense",
    "insert_expense",
    "list_expenses",
    "list_shares",
    "replace_shares",
    "sum_owed_by_participant",
    "sum_paid_by_participant",
    "update_expense",
]

UPDATABLE_COLUMNS: frozenset[str] = frozenset({"payer_id", "amount_minor", "note", "spot_id"})


def insert_expense(
    conn: sqlite3.Connection,
    *,
    expense_id: str,
    trip_id: str,
    payer_id: str,
    amount_minor: int,
    currency: str,
    note: str,
    spot_id: str | None,
    spent_at: str,
    created_at: str,
) -> None:
    conn.execute(
        "INSERT INTO expense (id, trip_id, payer_id, amount_minor, currency, note, spot_id,"
        " spent_at, version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (expense_id, trip_id, payer_id, amount_minor, currency, note, spot_id, spent_at, created_at),
    )


def get_expense(conn: sqlite3.Connection, trip_id: str, expense_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM expense WHERE id = ? AND trip_id = ?",
        (expense_id, trip_id),
    ).fetchone()


def list_expenses(conn: sqlite3.Connection, trip_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM expense WHERE trip_id = ? ORDER BY spent_at, id",
            (trip_id,),
        )
    )


def update_expense(
    conn: sqlite3.Connection,
    *,
    expense_id: str,
    expected_version: int,
    fields: Mapping[str, Any],
) -> bool:
    """낙관적 잠금 갱신 (NFR-012). 스팟과 같은 규칙 — `rowcount` 로만 판정한다."""
    unknown = set(fields) - UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"갱신할 수 없는 컬럼: {sorted(unknown)}")
    assignments = ", ".join(f"{column} = ?" for column in fields)
    prefix = f"{assignments}, " if assignments else ""
    sql = f"UPDATE expense SET {prefix}version = version + 1 WHERE id = ? AND version = ?"
    return conn.execute(sql, (*fields.values(), expense_id, expected_version)).rowcount == 1


def delete_expense(conn: sqlite3.Connection, expense_id: str) -> bool:
    return conn.execute("DELETE FROM expense WHERE id = ?", (expense_id,)).rowcount == 1


def replace_shares(conn: sqlite3.Connection, expense_id: str, shares: Mapping[str, int]) -> None:
    conn.execute("DELETE FROM expense_share WHERE expense_id = ?", (expense_id,))
    conn.executemany(
        "INSERT INTO expense_share (expense_id, participant_id, share_minor) VALUES (?, ?, ?)",
        [(expense_id, participant_id, share_minor) for participant_id, share_minor in sorted(shares.items())],
    )


def list_shares(conn: sqlite3.Connection, trip_id: str) -> dict[str, dict[str, int]]:
    """`{expense_id: {participant_id: share_minor}}`. 여행 전체를 한 번에 읽는다."""
    rows = conn.execute(
        "SELECT s.expense_id AS expense_id, s.participant_id AS participant_id, s.share_minor AS share_minor"
        " FROM expense_share s JOIN expense e ON e.id = s.expense_id"
        " WHERE e.trip_id = ? ORDER BY s.expense_id, s.participant_id",
        (trip_id,),
    )
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        grouped.setdefault(str(row["expense_id"]), {})[str(row["participant_id"])] = int(row["share_minor"])
    return grouped


def sum_paid_by_participant(conn: sqlite3.Connection, trip_id: str) -> dict[str, int]:
    return {
        str(row["payer_id"]): int(row["total"])
        for row in conn.execute(
            "SELECT payer_id, SUM(amount_minor) AS total FROM expense WHERE trip_id = ? GROUP BY payer_id",
            (trip_id,),
        )
    }


def sum_owed_by_participant(conn: sqlite3.Connection, trip_id: str) -> dict[str, int]:
    """정산은 **저장된** `share_minor` 를 그대로 더한다 — 재계산하지 않는다(§6.20)."""
    return {
        str(row["participant_id"]): int(row["total"])
        for row in conn.execute(
            "SELECT s.participant_id AS participant_id, SUM(s.share_minor) AS total"
            " FROM expense_share s JOIN expense e ON e.id = s.expense_id"
            " WHERE e.trip_id = ? GROUP BY s.participant_id",
            (trip_id,),
        )
    }
