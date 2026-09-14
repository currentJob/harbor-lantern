"""공용 픽스처 — 네트워크 차단 · 고정 시계 · 임시 DB · TestClient (설계서 §6.13 · §10).

세 담당(IMP-A 도메인 / IMP-B API·저장소 / IMP-C 프론트)이 모두 이 파일을 읽는다.
**여기서 픽스처 이름을 바꾸면 세 사람의 테스트가 동시에 깨진다.**

이 파일은 아직 존재하지 않는 모듈(`harbor_lantern.api.app`)을 **import 하지 않는다**.
T0 이 collect 에러를 내면 T1~T3 이 시작조차 못 한다 — 그래서 앱 관련 import 는 전부
픽스처 안으로 미루고, 없으면 그 테스트만 skip 한다.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from harbor_lantern.clock import FixedClock
from harbor_lantern.config import Settings, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = PROJECT_ROOT / "src" / "harbor_lantern" / "storage" / "schema.sql"
SEED_JSON = PROJECT_ROOT / "seed" / "spots.json"

# 고정 시각 = 2026-10-05 10:00 HKT (여행 1일차 오전). UTC 로는 02:00.
# 값 자체에 의미는 없지만 **바뀌면 안 된다** — 여러 테스트가 이 시각을 기준으로
# 기대값을 적는다.
FIXED_NOW = datetime(2026, 10, 5, 2, 0, 0, tzinfo=UTC)


# ─────────────────────────────────────────────────────────────────────────
# 네트워크 차단 (NFR-003 · AC-037)
# ─────────────────────────────────────────────────────────────────────────
class NetworkAccessBlocked(RuntimeError):
    """테스트 중 외부로 나가려 했다.

    "테스트가 네트워크에 의존하지 않는다"는 주석으로는 지킬 수 없다. 지키는 방법은
    **나가려는 순간 실패시키는 것** 하나뿐이다. 외부 호출이 필요하면 `tests/fakes.py`
    의 가짜 포트를 꽂아라(§2.2).
    """


_LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", ""}


def _is_local(host: object) -> bool:
    """루프백인가. 이름은 `localhost` 만, 숫자는 loopback/unspecified 만 통과."""
    if not isinstance(host, str):
        return False  # AF_UNIX 등 튜플이 아닌 주소는 통과시키지 않는다
    name = host.strip("[]").lower()
    if name in _LOCAL_HOSTNAMES:
        return True
    try:
        addr = ipaddress.ip_address(name)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_unspecified


def _host_of(address: Any) -> object:
    return address[0] if isinstance(address, tuple) and address else address


@pytest.fixture(scope="session", autouse=True)
def block_network() -> Iterator[None]:
    """세션 전체에서 루프백 밖으로 나가는 소켓 연결을 막는다.

    `TestClient` 는 ASGI 인프로세스 전송이라 소켓을 열지 않고 `sqlite3` 도 열지 않는다.
    그래서 이 차단에 걸리는 것은 **실수로 남은 진짜 아웃바운드 호출뿐**이다.

    `getaddrinfo` 까지 막는 이유: connect 만 막으면 DNS 조회에서 수 초를 버린 뒤에야
    실패한다. 느린 실패는 원인을 흐린다.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guard(address: Any, where: str) -> None:
        host = _host_of(address)
        if not _is_local(host):
            raise NetworkAccessBlocked(
                f"테스트가 외부 네트워크에 접근하려 했다: {where} → {address!r}. "
                "외부 공급자는 tests/fakes.py 의 가짜 포트로 대체하라 (NFR-003 · AC-037)."
            )

    def patched_connect(self: socket.socket, address: Any) -> Any:
        guard(address, "socket.connect")
        return real_connect(self, address)

    def patched_connect_ex(self: socket.socket, address: Any) -> Any:
        guard(address, "socket.connect_ex")
        return real_connect_ex(self, address)

    def patched_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        guard(address, "socket.create_connection")
        return real_create_connection(address, *args, **kwargs)

    def patched_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        guard(host, "socket.getaddrinfo")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = patched_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = patched_connect_ex  # type: ignore[method-assign]
    socket.create_connection = patched_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = patched_getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.create_connection = real_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────
# 시계 · 설정 · DB (DSN-03 · DSN-05)
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def fixed_clock() -> FixedClock:
    """`FIXED_NOW` 에 멈춘 시계. `advance()` 로만 움직인다(NFR-013)."""
    return FixedClock(FIXED_NOW)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트마다 새 DB 파일. 파일 DB 인 이유는 WAL·`busy_timeout` 동작이
    `:memory:` 와 다르기 때문이다 — 잠금을 검증하려면 진짜 파일이어야 한다."""
    return tmp_path / "harbor-lantern.db"


@pytest.fixture
def settings(db_path: Path) -> Settings:
    """임시 DB 를 가리키는 기본 설정.

    `env` 를 **명시적으로** 넘기므로 개발자 셸의 `HL_*` 가 테스트 결과를 바꾸지 못한다
    (NFR-013). 계수를 바꿔 보려면 `dataclasses.replace(settings, travel=...)`.
    """
    return load_settings(env={"HL_DB_PATH": str(db_path)})


@pytest.fixture
def schema_sql() -> str:
    return SCHEMA_SQL.read_text(encoding="utf-8")


@pytest.fixture
def sqlite_conn(db_path: Path, schema_sql: str) -> Iterator[sqlite3.Connection]:
    """스키마가 적용된 저수준 연결.

    리포지토리·서비스 테스트는 IMP-B 의 `storage/db.py` 를 쓰는 편이 낫다. 이건
    DDL 자체(제약·CASCADE·UNIQUE)를 확인할 때 쓰는 자리다.
    `foreign_keys` 는 **연결마다** 켜야 한다 — 기본이 OFF 다(§12 F5).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(schema_sql)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def seed_document() -> dict[str, Any]:
    """`seed/spots.json` 원본 (4일 27스팟 · AC-001)."""
    return json.loads(SEED_JSON.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────
# FastAPI 앱 (T2 산출물 — 아직 없으면 skip)
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def app(settings: Settings, fixed_clock: FixedClock) -> Any:
    """`create_app()` 으로 만든 앱.

    기대 계약: `create_app(settings=..., clock=...)` 이며 **인자 없이도** 호출된다
    (`uvicorn ... --factory` 가 그렇게 부른다 — 설계서 §9). 실제로는 시그니처를 보고
    받아 주는 인자만 넘기므로, IMP-B 가 이름을 다르게 두면 주입이 조용히 빠진다.
    """
    import inspect

    try:
        from harbor_lantern.api.app import create_app
    except ImportError:
        pytest.skip("harbor_lantern.api.app.create_app 이 아직 없다 (T2 · IMP-B)")

    params = inspect.signature(create_app).parameters
    kwargs: dict[str, Any] = {}
    if "settings" in params:
        kwargs["settings"] = settings
    if "clock" in params:
        kwargs["clock"] = fixed_clock
    return create_app(**kwargs)


@pytest.fixture
def client(app: Any) -> Iterator[Any]:
    """ASGI 인프로세스 `TestClient`. 소켓을 열지 않으므로 네트워크 차단에 걸리지 않는다."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
