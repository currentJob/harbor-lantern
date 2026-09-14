"""도메인 공용 유틸 — 반올림 · HKT 상수 · HH:MM ↔ 분 (설계서 §6.19 · §2.3 · §6.2).

순수 함수만 있다. 현재 시각을 읽지 않는다 — 시각이 필요하면 인자로 받는다(DSN-03).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal

__all__ = [
    "HKT",
    "MINUTES_PER_DAY",
    "UTC",
    "add_days",
    "day_offset",
    "format_hhmm",
    "format_iso_utc",
    "is_hhmm",
    "parse_hhmm",
    "parse_iso_utc",
    "round_half_up",
    "weekday_of",
]

# ── 시간대 ────────────────────────────────────────────────────────────────
# 홍콩은 UTC+8 고정이고 서머타임이 없다(가정 A9). 그래서 `zoneinfo` 를 쓰지 않는다 —
# `ZoneInfo("Asia/Hong_Kong")` 은 Windows 에서 `tzdata` 패키지를 추가로 요구해
# CI(리눅스)만 통과하고 개발자 PC 에서 터진다(설계서 §2.3 · §12 F10).
HKT = timezone(timedelta(hours=8), "HKT")
# `UTC` 는 `datetime.UTC` 를 그대로 재수출한다(`__all__` 참조) — 저장 규약이 UTC 라
# 호출부가 두 모듈을 오가지 않게 한 곳에 모아 둔다.

MINUTES_PER_DAY = 1440

# `HH:MM` 시간대 라벨 판정용 (설계서 §5.3 — 이 형태이면 고정시각 일정이다).
_HHMM_STRICT = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
# 파싱용. `24:00` 은 1440 분으로 읽는다(설계서 §6.9).
_HHMM_LOOSE = re.compile(r"^(\d{1,2}):([0-5][0-9])$")


def round_half_up(value: float | int) -> int:
    """0.5 를 **위로** 올린다 — JS `Math.round` 와 같은 결과.

    파이썬 내장 `round()` 는 은행가 반올림이라 `round(0.5) == 0`, `round(2.5) == 2` 다.
    진행률(`done/total*100`)이나 구간 분 계산을 내장 `round()` 로 하면 참조 HTML 의
    JS 값과 **특정 입력에서만** 갈린다 — 조용히 틀리는 종류다(설계서 §12 F4).

    구현은 `math.floor(value + 0.5)` 가 아니라 `Decimal` 을 쓴다. 전자는
    `0.49999999999999994 + 0.5` 가 이진 부동소수에서 정확히 `1.0` 이 되어 1 을 돌려준다.
    `Decimal(float)` 은 그 float 의 **정확한** 이진값을 담으므로 이 함정이 없다.

    동점은 +∞ 방향이다(`-1.5 → -1`). JS `Math.round(-1.5)` 도 -1 이다 —
    0 에서 멀어지는 쪽(`-2`)이 아니다.
    """
    d = Decimal(value)
    floor_value = d.to_integral_value(rounding=ROUND_FLOOR)
    return int(floor_value) + 1 if d - floor_value >= Decimal("0.5") else int(floor_value)


def is_hhmm(text: str) -> bool:
    """`HH:MM`(00:00~23:59) 형태인가. 시드 로더가 고정시각 스팟을 가려낼 때 쓴다(§5.3)."""
    return bool(_HHMM_STRICT.match(text.strip()))


def parse_hhmm(text: str) -> int | None:
    """`'09:00'` → `540`. 형태가 아니면 `None`.

    `'24:00'` 은 1440 을 돌려준다 — 영업시간 원문에 실제로 등장한다(설계서 §6.9).
    예외를 던지지 않는 이유: 입력이 사용자 편집 문자열이고, 호출부마다
    "못 읽으면 무엇으로 볼지"가 다르다(파서는 `unknown`, API 는 422).
    """
    m = _HHMM_LOOSE.match(text.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour == 24:
        return MINUTES_PER_DAY if minute == 0 else None
    if hour > 24:
        return None
    return hour * 60 + minute


def format_hhmm(minutes: int) -> str:
    """자정 기준 분 → `'HH:MM'`. 하루를 넘는 값은 **감아서** 시각만 낸다.

    `eta_min` 은 1440 을 넘을 수 있다(자정 넘김). 며칠째인지는 `day_offset()` 이
    따로 알려 준다 — 둘을 함께 실어야 API 경계에서 정보가 안 새어 나간다(§6.2).
    """
    minute_of_day = minutes % MINUTES_PER_DAY
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def day_offset(minutes: int) -> int:
    """자정 기준 분이 며칠째인가. `1500` → `1` (다음날 01:00)."""
    return minutes // MINUTES_PER_DAY


def weekday_of(date_iso: str) -> int:
    """`'2026-10-05'` → 0(월) ~ 6(일). `ClosedSpec.weekdays` 와 같은 규약이다(§6.2)."""
    return date.fromisoformat(date_iso).weekday()


def add_days(date_iso: str, days: int) -> str:
    """`'2026-10-05'`, `1` → `'2026-10-06'`. 여행 시작일에서 일자별 날짜를 만든다."""
    return (date.fromisoformat(date_iso) + timedelta(days=days)).isoformat()


def format_iso_utc(moment: datetime) -> str:
    """저장·전송용 UTC 표기 `'2026-10-05T01:23:45Z'` (설계서 §5.1).

    tz 정보가 없는 datetime 은 거부한다. "이게 UTC 겠지"라고 넘기는 순간
    8시간이 조용히 사라지거나 생긴다.
    """
    if moment.tzinfo is None:
        raise ValueError("naive datetime 은 저장할 수 없다 — tz-aware 로 넘겨라")
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc(text: str) -> datetime:
    """`format_iso_utc` 의 역함수. tz 가 없으면 UTC 로 본다(우리가 쓴 값만 들어온다)."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
