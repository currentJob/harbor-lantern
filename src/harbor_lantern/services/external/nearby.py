"""위치 키 TTL 캐시 — DSN-26 (REQ-017 · AC-054 · AC-055 · §6.13 · §12 F6).

**왜 `CachedProvider` 를 쓰지 않는가.** 그것은 키가 하나로 고정된 싱글턴이다(weather/fx —
인스턴스당 키 하나, 락 하나). 위치마다 키가 달라지는 질의를 그 안에 욱여넣으려면 키와
락을 인자로 받게 고쳐야 하고, 그 순간 날씨·환율 경로가 회귀 위험에 들어간다. 그래서
**같은 규칙을 따르는 별도 클래스**를 둔다.

`CachedResult` 는 그대로 재사용한다 — `available` / `stale` 의 뜻이 갈라지면 프론트가
두 가지 의미를 알아야 한다:

- `available=False`: 캐시도 없고 갱신도 실패했다. 이 화면만 비활성화한다.
- `stale=True`: 캐시가 만료됐는데 갱신에 실패해 **마지막 성공값**을 돌려줬다.

**좌표는 캐시 키를 만들 때 양자화한다**(기본 소수 3자리 ≈ 110m). 걸어 다니는 사용자의
좌표는 1초마다 몇 미터씩 바뀌는데, 그대로 키에 넣으면 캐시 적중률이 사실상 0 이 되고
매 요청이 공용 Overpass 로 나간다(그리고 429 를 받는다 — 실측으로 확인했다).

**키별 락**이 필요한 이유는 §12 F6 이 적어 둔 그대로다. 없으면 만료되는 순간 동시 요청이
각각 외부를 때리고, 그건 단일 요청 테스트로는 절대 안 잡힌다.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from typing import Any

from harbor_lantern.clock import Clock
from harbor_lantern.config import NearbyConfig
from harbor_lantern.domain.util import format_iso_utc, parse_iso_utc, round_half_up
from harbor_lantern.services.external.cache import CachedResult
from harbor_lantern.services.external.ports import PlaceSnapshot
from harbor_lantern.storage.db import Database
from harbor_lantern.storage.repo_cache import get_cache, put_cache

__all__ = ["NearbyProvider", "cache_key", "quantize"]


def quantize(value: float, digits: int) -> str:
    """캐시 키에 쓰는 좌표 문자열. `round_half_up` 으로 자릿수를 정수로 만든 뒤 조립한다.

    파이썬 내장 `round()`·`f"{x:.3f}"` 는 은행가 반올림이라 경계값에서만 갈린다(§12 F4).
    캐시 키는 "가끔 다른 키가 나오는" 것만으로 충분히 나쁘다 — 그 요청만 조용히 느려진다.
    """
    scale = 10**digits
    units = round_half_up(value * scale)
    sign = "-" if units < 0 else ""
    units = abs(units)
    return f"{sign}{units // scale}.{units % scale:0{digits}d}" if digits > 0 else f"{sign}{units}"


def cache_key(categories: tuple[str, ...], lat: float, lng: float, radius_m: int, digits: int) -> str:
    """`nearby:{카테고리}:{lat_q}:{lng_q}:{반경}`.

    카테고리는 **정렬해서** 넣는다 — `?category=cafe&category=restaurant` 와
    `?category=restaurant&category=cafe` 는 같은 질의이고 같은 캐시를 써야 한다.
    """
    names = ",".join(sorted(categories))
    return f"nearby:{names}:{quantize(lat, digits)}:{quantize(lng, digits)}:{int(radius_m)}"


class NearbyProvider:
    """`NearbyPort` 하나를 감싸는 **위치 키** TTL 캐시. 저장은 기존 `external_cache` 테이블."""

    def __init__(self, port: Any, cfg: NearbyConfig, db: Database, clock: Clock) -> None:
        self._port = port
        self._cfg = cfg
        self._db = db
        self._clock = clock
        # 키마다 락 하나. 이 dict 자체를 지키는 락이 따로 필요하다 — 두 스레드가 동시에
        # `setdefault` 하는 것은 안전하지만, 검사 후 생성으로 쓰면 락이 두 개 생긴다.
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @property
    def port(self) -> Any:
        return self._port

    def get(self, *, categories: tuple[str, ...], lat: float, lng: float, radius_m: int) -> CachedResult:
        key = cache_key(categories, lat, lng, radius_m, self._cfg.quantize_digits)
        now = self._clock.now_utc()
        cached = self._read(key)
        if cached is not None and self._fresh(cached, now):
            return CachedResult(cached[0], cached[1], available=True, stale=False)

        with self._lock_for(key):
            # 더블 체크 — 락을 기다리는 사이에 다른 스레드가 이미 갱신했을 수 있다.
            cached = self._read(key)
            if cached is not None and self._fresh(cached, self._clock.now_utc()):
                return CachedResult(cached[0], cached[1], available=True, stale=False)

            try:
                places = self._port.fetch(categories=categories, lat=lat, lng=lng, radius_m=radius_m)
                payload = _to_payload(places)
            except Exception:
                # 넓게 잡는다(§6.13 과 같은 이유). 여기서 새어 나간 예외 하나가 500 이 되고,
                # 그 500 은 "근처 맛집" 버튼 하나 때문에 화면에 붉은 에러를 띄운다(NFR-004).
                if cached is not None:
                    return CachedResult(cached[0], cached[1], available=True, stale=True)
                return CachedResult(None, None, available=False, stale=False)

            fetched_at = format_iso_utc(self._clock.now_utc())
            self._write(key, payload, fetched_at)
            return CachedResult(payload, fetched_at, available=True, stale=False)

    # ── 내부 ─────────────────────────────────────────────────────────────
    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _read(self, key: str) -> tuple[dict[str, Any], str] | None:
        with self._db.connection() as conn:
            row = get_cache(conn, key)
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            return None  # 손상된 캐시 행은 없는 것으로 본다 (다음 성공이 덮어쓴다)
        if not isinstance(payload, dict) or not isinstance(payload.get("places"), list):
            return None
        return payload, str(row["fetched_at"])

    def _write(self, key: str, payload: dict[str, Any], fetched_at: str) -> None:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._db.connection() as conn:
            put_cache(conn, key, blob, fetched_at)

    def _fresh(self, cached: tuple[dict[str, Any], str], now: Any) -> bool:
        try:
            fetched_at = parse_iso_utc(cached[1])
        except ValueError:
            return False
        return (now - fetched_at).total_seconds() < self._cfg.ttl_s


def _to_payload(places: Any) -> dict[str, Any]:
    """스냅샷들 → 캐시에 넣을 dict. 공급자 원문이 아니라 우리 형태다(§6.13)."""
    items = []
    for place in places or ():
        if isinstance(place, PlaceSnapshot):
            items.append(asdict(place))
        elif isinstance(place, dict):
            items.append(dict(place))
        else:
            raise TypeError(f"장소 스냅샷을 직렬화할 수 없다: {type(place)!r}")
    return {"places": items}
