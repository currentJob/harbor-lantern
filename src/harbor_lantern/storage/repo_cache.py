"""`external_cache` · `join_attempt` 리포지토리 (설계서 §6.13 · §6.17).

레이트 리밋 카운터를 **DB 테이블**에 두는 이유는 둘이다(§6.17): 시계를 주입하면
그대로 결정론 테스트가 되고(AC-040), 프로세스 재시작으로 카운터가 리셋되지 않는다.
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "count_recent_attempts",
    "get_cache",
    "oldest_attempt_at",
    "prune_attempts",
    "put_cache",
    "record_attempt",
]


# ── external_cache ────────────────────────────────────────────────────────
def get_cache(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM external_cache WHERE key = ?", (key,)).fetchone()


def put_cache(conn: sqlite3.Connection, key: str, payload: str, fetched_at: str) -> None:
    """`payload` 는 **정규화된 우리 형태**의 JSON 이다(공급자 원문이 아니다 — §6.13).

    공급자가 필드를 바꿔도 캐시에 남은 과거 데이터가 계속 읽히게 하려면 이 경계가 필요하다.
    """
    conn.execute(
        "INSERT INTO external_cache (key, payload, fetched_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at",
        (key, payload, fetched_at),
    )


# ── join_attempt (IP 레이트 리밋 · NFR-006) ───────────────────────────────
def count_recent_attempts(conn: sqlite3.Connection, ip: str, since: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM join_attempt WHERE ip = ? AND attempted_at >= ?",
        (ip, since),
    ).fetchone()
    return int(row["n"])


def oldest_attempt_at(conn: sqlite3.Connection, ip: str, since: str) -> str | None:
    row = conn.execute(
        "SELECT MIN(attempted_at) AS oldest FROM join_attempt WHERE ip = ? AND attempted_at >= ?",
        (ip, since),
    ).fetchone()
    return None if row is None or row["oldest"] is None else str(row["oldest"])


def record_attempt(conn: sqlite3.Connection, ip: str, attempted_at: str) -> None:
    """성공·실패 무관하게 기록한다 — 실패만 세면 유효한 코드로 열거를 계속할 수 있다."""
    conn.execute("INSERT INTO join_attempt (ip, attempted_at) VALUES (?, ?)", (ip, attempted_at))


def prune_attempts(conn: sqlite3.Connection, before: str) -> int:
    """창 밖의 기록은 같은 트랜잭션에서 지운다(§6.17) — 테이블이 무한히 자라지 않게."""
    return conn.execute("DELETE FROM join_attempt WHERE attempted_at < ?", (before,)).rowcount
