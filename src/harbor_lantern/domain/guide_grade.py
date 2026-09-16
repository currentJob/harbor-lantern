"""선별과 등급 판정 — DSN-49 · DSN-38 (설계서 §16.9 · REQ-029 · AC-083 · AC-084).

**등급 지표는 후보 전체가 아니라 실제로 쓰는 스팟 집합에서 잰다**(DSN-49). 34개 도시
측정이 그 자리를 옮겼다 — 베네치아는 후보 80건 기준 한국어 라벨 35% 지만 **상위 25건
기준 84%** 다(산마르코 대성당·두칼레 궁전·리알토 교·탄식의 다리는 전부 한국어가 있다).
꼬리 55건은 3일 일정의 슬롯 15개에 **한 번도 배정되지 않는다.** 사용자가 볼 일 없는
데이터가 사용자가 보는 표시를 바꾸게 두지 않는다.

반대로 발리는 상위 25건만 봐도 20%다 — **선별로 가려지지 않는 진짜 미달**이다. 그래서
이 보정은 기준을 느슨하게 하는 것이 아니라 **재는 자리를 옳게 옮기는 것**이다.

**`grade_city` 는 후보 목록을 받지 않는다.** 받는 것은 분류·검토·선별이 끝난 `selected`
하나뿐이다 — 후보를 넘길 경로를 시그니처로 없앤다. 주석으로 적어 둔 규칙은 그것을 쓴
사람에게만 규칙이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "BELOW_MAX_RATIO",
    "BELOW_MAX_SPOTS",
    "EXPORT_MAX",
    "FULL_MIN_RATIO",
    "FULL_MIN_SPOTS",
    "GRADE_WINDOW",
    "CityGrade",
    "grade",
    "grade_city",
    "select_spots",
]

# 등급 평가 집합 크기. 기본 페이스 5스팟/일 × 5일 (§16.9.1).
GRADE_WINDOW = 25
# 수록 상한. 그 이상은 일정에 쓰이지 않는다 (R9 저장소 부피).
EXPORT_MAX = 60

# 완전의 하한(A16): 기본 페이스 5스팟/일 × 3일 = 15. `planner` 의 페이스 정의에서 나온 수치다.
FULL_MIN_SPOTS = 15
FULL_MIN_RATIO = 0.80
BELOW_MAX_SPOTS = 8
BELOW_MAX_RATIO = 0.50


@dataclass(frozen=True)
class CityGrade:
    """도시 하나의 등급과 그 근거 지표. 그대로 `harvest` 블록에 기록된다(§16.11).

    `spot_count` 는 `selected` **전량**이고 `ko_label_ratio` 는 **상위 `grade_window`** 안의
    비율이다. 개수는 일정을 채울 수 있느냐의 문제라 꼬리도 세고, 품질 비율은 쓰는 자리에서만 센다.
    """

    grade: Literal["full", "partial", "below"]
    spot_count: int
    ko_label_count: int
    ko_label_ratio: float
    grade_window: int


def grade(spot_count: int, ko_label_ratio: float) -> Literal["full", "partial", "below"]:
    """AC-083 의 판정 규칙. **적용 순서가 규칙의 일부다** — 미달을 먼저 본다.

    1. `spot_count < 8` **또는** `ko_label_ratio < 0.50` → 미달(수록하지 않는다)
    2. 아니고 `spot_count >= 15` **그리고** `ko_label_ratio >= 0.80` → 완전
    3. 그 외 → 부분

    등급은 셋뿐이다. 정찰이 쓴 '완전−' 같은 중간 등급을 만들지 않는다 — 화면이 밝혀야
    하는 것은 세 가지이고(AC-085), 넷으로 늘리면 문구도 넷이 되어 사용자가 구분하지 못한다.
    """
    if spot_count < BELOW_MAX_SPOTS or ko_label_ratio < BELOW_MAX_RATIO:
        return "below"
    return "full" if (spot_count >= FULL_MIN_SPOTS and ko_label_ratio >= FULL_MIN_RATIO) else "partial"


def grade_city(selected: Sequence[Mapping[str, Any]]) -> CityGrade:
    """선별이 끝난 스팟 목록으로 지표를 **스스로 계산하고** 등급을 낸다 (DSN-49 · §16.9.1).

    비율은 `round(x, 3)` 한 값으로 등급을 매긴다. 기록되는 값과 판정에 쓰인 값이 같아야
    "기록된 지표로 다시 계산한 등급이 기록된 등급과 같다"(AC-083 후단)가 성립한다 —
    반올림 전 값으로 판정하면 0.7996 이 `0.8` 로 기록된 채 '부분' 으로 남아, 재계산이
    '완전' 을 내고 데이터셋 테스트가 원인 없이 빨개진다.

    `name_source == "ko"` 만 한국어로 센다. 폴백으로 채운 이름(영어·원어)을 한국어로 세면
    비율이 스스로를 증명하게 된다(§16.11 이 `name_source` 를 따로 둔 이유다).
    """
    ordered = select_spots(selected, limit=len(selected))
    window = ordered[:GRADE_WINDOW]
    ko_count = sum(1 for spot in window if spot.get("name_source") == "ko")
    ratio = round(ko_count / len(window), 3) if window else 0.0
    return CityGrade(
        grade=grade(len(ordered), ratio),
        spot_count=len(ordered),
        ko_label_count=ko_count,
        ko_label_ratio=ratio,
        grade_window=len(window),
    )


def select_spots(
    reviewed: Sequence[Mapping[str, Any]],
    limit: int = EXPORT_MAX,
) -> tuple[Mapping[str, Any], ...]:
    """검토 통과분을 중요도 순으로 정렬해 상위 `limit` 까지 (§16.9.1).

    정렬은 `(-sitelinks, wikidata_id)` **고정**이다(§16.6 과 같은 키). 이 순서가 등급
    평가 집합을 자르는 자리이자 일정의 예약 순서(AC-068)이므로, 두 곳이 다른 순서를
    쓰면 "상위 25건"이 무엇을 가리키는지가 파일마다 달라진다.
    """
    ordered = sorted(reviewed, key=lambda spot: (-_sitelinks_of(spot), _qid_of(spot)))
    return tuple(ordered[: max(0, limit)])


def _sitelinks_of(spot: Mapping[str, Any]) -> int:
    importance = spot.get("importance")
    if isinstance(importance, Mapping):
        return int(importance.get("sitelinks", 0) or 0)
    return int(spot.get("sitelinks", 0) or 0)


def _qid_of(spot: Mapping[str, Any]) -> str:
    return str(spot.get("wikidata_id") or spot.get("id") or "")
