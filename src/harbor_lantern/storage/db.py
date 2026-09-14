"""SQLite 연결 · PRAGMA · 트랜잭션 경계 — DSN-05 (설계서 §5.2 · §12 F5).

**PRAGMA 는 연결마다 건다.** `foreign_keys` 는 SQLite 기본이 OFF 이고, 켜지 않아도
테스트가 전부 통과한다 — CASCADE 만 조용히 안 돌아 고아 행이 쌓인다(§12 F5).
`schema.sql` 앞머리에 같은 PRAGMA 4줄이 있지만 `executescript` 로 실행한 PRAGMA 는
트랜잭션 상태에 따라 무시될 수 있으므로, 연결 직후 **따로 한 번 더** 건다.

트랜잭션은 `BEGIN IMMEDIATE` 로 연다. SQLite 기본 `DEFERRED` 는 첫 쓰기에서야 라이터
락을 잡아, 읽고-쓰는 사이에 다른 라이터가 끼면 `SQLITE_BUSY` 가 **늦게** 터진다(§6.11).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["PRAGMAS", "SCHEMA_PATH", "Database", "transaction"]

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# 설계서 §5.2 의 4줄. 순서도 그대로다 — WAL 전환이 먼저여야 synchronous 가 의미를 갖는다.
PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
)


class Database:
    """DB 파일 하나. 연결은 **요청(작업)마다 새로** 만들고 끝나면 닫는다.

    `check_same_thread=False` 인 이유: FastAPI 는 동기(`def`) 핸들러와 동기 의존성을
    각각 스레드풀에서 돌리므로 둘이 다른 스레드일 수 있다. 연결을 **공유하지 않고**
    요청 단위로 만들기 때문에 동시 사용은 일어나지 않는다.
    """

    __slots__ = ("path",)

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,  # 자동 커밋. 트랜잭션은 우리가 명시적으로 연다
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        for pragma in PRAGMAS:
            conn.execute(pragma)
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def apply_schema(self) -> None:
        """`schema.sql` 을 멱등 적용한다(설계서 §9 — 별도 마이그레이션 도구 없음)."""
        if self.path.parent and str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        ddl = SCHEMA_PATH.read_text(encoding="utf-8")
        conn = self.connect()
        try:
            conn.executescript(ddl)
            # executescript 안의 PRAGMA 는 트랜잭션 상태에 따라 무시될 수 있다(§12 F5).
            for pragma in PRAGMAS:
                conn.execute(pragma)
        finally:
            conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE` … `COMMIT`. 예외가 나면 통째로 `ROLLBACK` 한다(AC-047)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
