"""참여 시도 IP 레이트 리밋 — DSN-08 (설계서 §6.17 · NFR-006 · AC-040).

**코드 유효성을 보기 전에** 막는다. 순서가 뒤집히면 공격자는 429 를 받기 전에 코드가
맞는지 아닌지를 이미 알게 된다.

카운터는 인메모리가 아니라 **DB 테이블**이다. 이유 둘: 시계를 주입하면 그대로 결정론
테스트가 되고, 프로세스 재시작으로 카운터가 리셋되지 않는다.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from harbor_lantern.api.errors import RateLimitedError
from harbor_lantern.clock import Clock
from harbor_lantern.config import Settings
from harbor_lantern.domain.util import format_iso_utc, parse_iso_utc
from harbor_lantern.storage import repo_cache

__all__ = ["client_ip", "enforce_join_rate_limit"]


def client_ip(request: object) -> str:
    """프록시 헤더는 **믿지 않는다** — 클라이언트가 마음대로 쓸 수 있는 값으로 리밋을
    걸면 리밋이 없는 것과 같다. 단일 인스턴스 로컬 실행(A6)이라 소켓 주소면 충분하다."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return str(host) if host else "unknown"


def enforce_join_rate_limit(
    conn: sqlite3.Connection,
    clock: Clock,
    settings: Settings,
    ip: str,
) -> None:
    """임계 초과면 `RateLimitedError`(429 + `Retry-After`). 통과하면 시도를 기록한다.

    기록은 **자동 커밋**으로 남긴다(트랜잭션 안에서 하면 뒤의 실패와 함께 롤백돼
    실패한 시도가 세어지지 않는다 — 그러면 리밋이 아무것도 막지 못한다).
    """
    now = clock.now_utc()
    window_start = format_iso_utc(now - timedelta(seconds=settings.join_rate_limit_window_s))
    repo_cache.prune_attempts(conn, window_start)

    attempts = repo_cache.count_recent_attempts(conn, ip, window_start)
    if attempts >= settings.join_rate_limit_n:
        oldest = repo_cache.oldest_attempt_at(conn, ip, window_start)
        elapsed = 0.0 if oldest is None else (now - parse_iso_utc(oldest)).total_seconds()
        retry_after = max(1, int(settings.join_rate_limit_window_s - elapsed) + 1)
        raise RateLimitedError(retry_after)

    repo_cache.record_attempt(conn, ip, format_iso_utc(now))
