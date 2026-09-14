"""TTL 캐시 · single-flight · stale 폴백 — DSN-20 (설계서 §6.13 · §12 F6).

흐름은 다섯 단계다.

1. `external_cache` 에서 키를 읽는다.
2. 있고 `now - fetched_at < ttl` → **어댑터를 부르지 않고** 반환 (AC-026 의 "호출 0회").
3. 만료·없음 → **키별 락을 잡고 다시 확인**(더블 체크) 후 `port.fetch()` 를 1회.
4. 성공 → 정규화 JSON 을 캐시에 쓰고 `stale=False`.
5. 실패 → **예외를 밖으로 내지 않는다.** 캐시가 있으면 `stale=True`, 없으면
   `available=False`. 어떤 경우에도 5xx 를 만들지 않는다(NFR-004 · AC-027 · AC-038).

**락이 왜 필요한가.** 없으면 만료되는 순간 동시 요청 n건이 각각 외부를 때린다.
단일 요청 테스트로는 절대 안 잡히고, AC-026 이 부하에서만 깨진다(§12 F6).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from harbor_lantern.clock import Clock
from harbor_lantern.domain.util import format_iso_utc, parse_iso_utc
from harbor_lantern.services.external.ports import snapshot_to_payload
from harbor_lantern.storage.db import Database
from harbor_lantern.storage.repo_cache import get_cache, put_cache

__all__ = ["CachedProvider", "CachedResult"]


@dataclass(frozen=True)
class CachedResult:
    """`available` · `stale` 의 뜻을 섞지 말 것.

    - `available=False`: 캐시도 없고 갱신도 실패했다. 위젯만 비활성화한다.
    - `stale=True`: 캐시가 만료됐는데 갱신에 실패해 **마지막 성공값**을 돌려줬다.
    """

    payload: dict[str, Any] | None
    fetched_at: str | None
    available: bool
    stale: bool

    def as_meta(self) -> dict[str, Any]:
        return {"available": self.available, "stale": self.stale, "fetched_at": self.fetched_at}


class CachedProvider:
    """포트 하나를 감싸는 TTL 캐시. 키마다 인스턴스 하나(=락 하나)를 둔다."""

    def __init__(
        self,
        port: Any,
        key: str,
        ttl_s: int,
        db: Database,
        clock: Clock,
        lock: threading.Lock | None = None,
    ) -> None:
        self._port = port
        self._key = key
        self._ttl_s = ttl_s
        self._db = db
        self._clock = clock
        self._lock = lock if lock is not None else threading.Lock()

    @property
    def port(self) -> Any:
        return self._port

    def get(self) -> CachedResult:
        now = self._clock.now_utc()
        cached = self._read()
        if cached is not None and self._fresh(cached, now):
            return CachedResult(cached[0], cached[1], available=True, stale=False)

        with self._lock:
            # 더블 체크 — 락을 기다리는 사이에 다른 스레드가 이미 갱신했을 수 있다.
            cached = self._read()
            if cached is not None and self._fresh(cached, self._clock.now_utc()):
                return CachedResult(cached[0], cached[1], available=True, stale=False)

            try:
                snapshot = self._port.fetch()
                payload = snapshot_to_payload(snapshot)
            except Exception:
                # 넓게 잡는다. 여기서 새어 나간 예외 하나가 500 이 되고, 그 500 이
                # 날씨 위젯 때문에 일정 화면 전체를 못 쓰게 만든다(NFR-004).
                if cached is not None:
                    return CachedResult(cached[0], cached[1], available=True, stale=True)
                return CachedResult(None, None, available=False, stale=False)

            fetched_at = format_iso_utc(self._clock.now_utc())
            self._write(payload, fetched_at)
            return CachedResult(payload, fetched_at, available=True, stale=False)

    # ── 내부 ─────────────────────────────────────────────────────────────
    def _read(self) -> tuple[dict[str, Any], str] | None:
        with self._db.connection() as conn:
            row = get_cache(conn, self._key)
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            return None  # 손상된 캐시 행은 없는 것으로 본다 (다음 성공이 덮어쓴다)
        if not isinstance(payload, dict):
            return None
        return payload, str(row["fetched_at"])

    def _write(self, payload: dict[str, Any], fetched_at: str) -> None:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._db.connection() as conn:
            put_cache(conn, self._key, blob, fetched_at)

    def _fresh(self, cached: tuple[dict[str, Any], str], now: Any) -> bool:
        try:
            fetched_at = parse_iso_utc(cached[1])
        except ValueError:
            return False
        return (now - fetched_at).total_seconds() < self._ttl_s
