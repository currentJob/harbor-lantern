"""시계 포트 — DSN-03 (설계서 §2.3 · NFR-013).

시각을 읽는 곳은 여기 하나다. 아무 데서나 `datetime.now()` 를 부르면 그 코드는
**테스트할 수 없는 코드**가 된다 — 결정론 테스트(NFR-003 · NFR-013)의 토대가 이 포트다.

`domain/` 은 시계를 **받지 않는다**. 필요한 시각은 인자로 들어온다(§2.3).
서비스·API 계층만 `Clock` 을 주입받는다.

HKT 변환은 `zoneinfo` 를 쓰지 않는다 — `harbor_lantern.domain.util.HKT`(UTC+8 고정)를
쓴다(§2.3 · §12 F10).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "FixedClock", "SystemClock"]


@runtime_checkable
class Clock(Protocol):
    """지금이 언제인가. 반환은 **항상 tz-aware UTC** 다."""

    def now_utc(self) -> datetime: ...


class SystemClock:
    """운영용. 프로세스가 도는 기계의 시계를 읽는다."""

    __slots__ = ()

    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """테스트용. 멈춰 있고, `advance()` 로만 움직인다.

    캐시 TTL(AC-026)·레이트 리밋 창(AC-040)처럼 "시간이 지나면" 을 검증하는 테스트가
    `sleep` 을 쓰기 시작하면 스위트가 느려지고 결과가 기계 성능에 흔들린다.
    """

    __slots__ = ("_instant",)

    def __init__(self, instant: datetime) -> None:
        self._instant = _require_aware(instant)

    def now_utc(self) -> datetime:
        return self._instant

    def advance(self, delta: timedelta | float) -> datetime:
        """시계를 앞으로 민다. 숫자를 주면 초로 본다. 옮긴 뒤의 시각을 돌려준다."""
        step = delta if isinstance(delta, timedelta) else timedelta(seconds=delta)
        self._instant = self._instant + step
        return self._instant

    def set(self, instant: datetime) -> datetime:
        self._instant = _require_aware(instant)
        return self._instant

    def __repr__(self) -> str:  # 테스트 실패 메시지에 시각이 보이게 한다
        return f"FixedClock({self._instant.isoformat()})"


def _require_aware(instant: datetime) -> datetime:
    """naive datetime 을 받지 않는다.

    "이건 UTC 겠지"라고 넘기는 순간 8시간이 조용히 사라지거나 생긴다. 그리고 그 오차는
    타임라인 계산을 지나 화면에 도달할 때까지 아무 예외도 만들지 않는다.
    """
    if instant.tzinfo is None:
        raise ValueError("FixedClock 은 tz-aware datetime 만 받는다 (UTC 로 넘겨라)")
    return instant.astimezone(UTC)
