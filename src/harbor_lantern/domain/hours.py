"""영업시간 파서 — DSN-16 (설계서 §6.9 · REQ-012 · O4 · AC-022 · AC-024).

**대원칙 (R1 · AC-024): 못 읽으면 `unknown` 이고, `unknown` 은 경고를 만들지 않는다.**

이 모듈의 실패 방식은 하나뿐이다 — *너무 잘 읽으려고 애쓰는 것*. 거짓 경고 하나가
진짜 경고 전부를 무시하게 만든다(§12 F9). 시드 문자열은 A1 에 따라 근사치이고
자유 서식이다. 아래 규칙 7개 밖의 문자열은 **전부 `unknown` 으로 둔다.**

규칙은 위에서부터 순서대로 적용하고 처음 맞는 것을 채택한다. P-02(괄호 제거)만
판정이 아니라 전처리이므로, `HoursSpec.pattern` 에는 **판정을 낸 규칙**만 담긴다.
"""

from __future__ import annotations

import re
import unicodedata

from harbor_lantern.domain.models import ClosedSpec, HoursSpec, OpenState
from harbor_lantern.domain.util import MINUTES_PER_DAY, parse_hhmm

__all__ = ["APPROXIMATE_MARKERS", "is_open_at", "parse_closed", "parse_hours"]

# 전처리: 대시 계열을 전부 ASCII '-' 로 모은다. 시드에는 EN DASH(U+2013)와
# TILDE(U+007E)가 실제로 등장한다.
_DASHES = "–—−~〜"
_DASH_TABLE = str.maketrans(dict.fromkeys(_DASHES, "-"))

# 세그먼트 구분자. 시드에는 SOLIDUS 와 MIDDLE DOT(U+00B7)이 쓰인다.
_SEGMENT_SPLIT = re.compile(r"[/·ㆍ・]")

_TIME = re.compile(r"\d{1,2}:\d{2}")
_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_PARENS = re.compile(r"\([^)]*\)")

# P-01: `평일 A-B / (주말·공휴일|주말|공휴일) C-D`
_WEEKDAY_WEEKEND = re.compile(
    r"평일\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})"
    r"\s*[/·ㆍ・]\s*"
    r"(?:주말\s*[·ㆍ・]?\s*공휴일|주말|공휴일)\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})"
)

# '상시' 는 24시간 개방, '24h' 는 괄호 보조 표기로 등장한다.
_ALWAYS_OPEN_MARKERS = ("상시", "24h", "24시간")

# AC-022 — 같은 시각으로 파싱하되 근사 플래그를 붙인다.
APPROXIMATE_MARKERS = ("대략", "약", "대체로", "경")

_WEEKDAY_TOKENS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_CLOSED_WEEKDAY = re.compile(r"([월화수목금토일])요일")

_UNKNOWN_CACHE = {
    "P-03": HoursSpec(status="unknown", pattern="P-03"),
    "P-06": HoursSpec(status="unknown", pattern="P-06"),
    "P-07": HoursSpec(status="unknown", pattern="P-07"),
}


def _normalize(text: str) -> str:
    """NFKC → 대시 통일 → 연속 공백 축약 (설계서 §6.9 전처리)."""
    normalized = unicodedata.normalize("NFKC", text).translate(_DASH_TABLE)
    return re.sub(r"\s+", " ", normalized).strip()


def _is_approximate(text: str) -> bool:
    return any(marker in text for marker in APPROXIMATE_MARKERS)


def _has_time_expression(segment: str) -> bool:
    """이 세그먼트가 '시간 표현'(범위 또는 상시)을 가졌는가 — P-03 의 판정 단위."""
    if _RANGE.search(segment):
        return True
    lowered = segment.lower()
    return any(marker in lowered for marker in _ALWAYS_OPEN_MARKERS)


def parse_hours(text: str) -> HoursSpec:
    """자유 문자열 영업시간 → `HoursSpec`. 읽을 수 없으면 `status="unknown"`.

    시드 27건 전부를 이 함수에 넣어 본 결과는 `tests/domain/test_hours.py` 가
    표로 고정한다 — 그 27건이 이 파서의 **실제 코퍼스**다.
    """
    normalized = _normalize(text)
    if not normalized:
        return _UNKNOWN_CACHE["P-07"]

    # ── P-01: 평일 / 주말·공휴일 요일별 개방 ──────────────────────────────
    weekday_match = _WEEKDAY_WEEKEND.search(normalized)
    if weekday_match:
        weekday_open = _weekday_open_from(weekday_match.groups())
        if weekday_open is not None:
            return HoursSpec(
                status="open_range",
                open_min=None,  # 요일마다 다르다 — 하나로 뭉치면 거짓말이 된다
                close_min=None,
                crosses_midnight=False,
                approximate=_is_approximate(normalized),
                weekday_open=weekday_open,
                pattern="P-01",
            )

    # ── P-02: 괄호 세그먼트 제거 후 재평가 (판정이 아니라 전처리) ──────────
    body = re.sub(r"\s+", " ", _PARENS.sub(" ", normalized)).strip()
    approximate = _is_approximate(body)

    # ── P-03: 시간 표현을 가진 세그먼트가 2개 이상 → 주체가 둘이다 ─────────
    segments = [seg.strip() for seg in _SEGMENT_SPLIT.split(body) if seg.strip()]
    if sum(1 for seg in segments if _has_time_expression(seg)) >= 2:
        return _UNKNOWN_CACHE["P-03"]

    # ── P-04: HH:MM-HH:MM 범위가 정확히 1개 ───────────────────────────────
    ranges = _RANGE.findall(body)
    if len(ranges) == 1:
        open_min = parse_hhmm(ranges[0][0])
        close_min = parse_hhmm(ranges[0][1])
        if open_min is not None and close_min is not None:
            return HoursSpec(
                status="open_range",
                open_min=open_min,
                close_min=close_min,
                crosses_midnight=close_min <= open_min,
                approximate=approximate,
                weekday_open=None,
                pattern="P-04",
            )

    # ── P-05: 범위 0개 + 상시/24h ─────────────────────────────────────────
    if not ranges and any(marker in body.lower() for marker in _ALWAYS_OPEN_MARKERS):
        return HoursSpec(
            status="open_24h",
            open_min=0,
            close_min=MINUTES_PER_DAY,
            crosses_midnight=False,
            approximate=approximate,
            weekday_open=None,
            pattern="P-05",
        )

    # ── P-06: HH:MM 이 하나뿐이고 범위가 아니다 → 이벤트 시작시각이다 ──────
    if not ranges and len(_TIME.findall(body)) == 1:
        return _UNKNOWN_CACHE["P-06"]

    # ── P-07: 그 외 전부 ──────────────────────────────────────────────────
    return _UNKNOWN_CACHE["P-07"]


def _weekday_open_from(groups: tuple[str, ...]) -> dict[int, tuple[int, int]] | None:
    """P-01 의 네 시각 → 요일별 개방 구간. 하나라도 못 읽으면 `None`(→ 다음 규칙으로)."""
    parsed = [parse_hhmm(raw) for raw in groups]
    if any(value is None for value in parsed):
        return None
    minutes = [value for value in parsed if value is not None]
    weekday_open_min, weekday_close_min, weekend_open_min, weekend_close_min = minutes
    table: dict[int, tuple[int, int]] = {day: (weekday_open_min, weekday_close_min) for day in range(5)}
    table[5] = (weekend_open_min, weekend_close_min)
    table[6] = (weekend_open_min, weekend_close_min)
    return table


def parse_closed(text: str) -> ClosedSpec:
    """휴무 문자열 → 요일 집합. `'요일'` 토큰만 본다 (설계서 §6.9).

    `"(정비휴무 9/8~18은 여행 전 종료)"` 처럼 **요일 토큰이 없으면 휴무 없음**이다 —
    날짜 범위는 해석하지 않는다. 값이 A1 의 근사치이므로, 틀린 경고를 만드는 것보다
    안 만드는 쪽이 낫다.
    """
    normalized = _normalize(text)
    found = {_WEEKDAY_TOKENS[token] for token in _CLOSED_WEEKDAY.findall(normalized)}
    return ClosedSpec(weekdays=frozenset(found))


def is_open_at(spec: HoursSpec, minute_of_day: int, weekday: int) -> OpenState:
    """그 시각에 열려 있는가 — `"open"` · `"closed"` · `"unknown"`.

    `unknown` 은 "닫혔다"가 아니다. 호출부(`warn.build_warnings`)는 `unknown` 에
    **경고를 만들지 않는다**(AC-024).

    **경계 규약: 개방 구간은 양끝 포함 `[open, close]` 다.** AC-023 이 경고 조건을
    "도착 예상시각이 마감시각 **이후**"로 적었으므로, 마감시각 정각 도착은 경고가 아니다.
    경계에서 굳이 경고를 하나 더 만드는 쪽을 고르지 않는다 — 이 파일의 대원칙이다.
    """
    if spec.status == "unknown":
        return "unknown"
    if spec.status == "open_24h":
        return "open"

    window = _window_for(spec, weekday)
    if window is None:
        return "unknown"

    open_min, close_min = window
    moment = minute_of_day % MINUTES_PER_DAY
    if close_min <= open_min:  # 자정을 넘긴다 (예: 05:50-00:48)
        return "open" if (moment >= open_min or moment <= close_min) else "closed"
    return "open" if open_min <= moment <= close_min else "closed"


def _window_for(spec: HoursSpec, weekday: int) -> tuple[int, int] | None:
    """그 요일의 개방 구간. 요일 표에 그 요일이 없으면 `None`(→ `unknown`)."""
    if spec.weekday_open is not None:
        return spec.weekday_open.get(weekday)
    if spec.open_min is None or spec.close_min is None:
        return None
    return spec.open_min, spec.close_min
