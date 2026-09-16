"""연결 검증 — DSN-37 (설계서 §16.8 · REQ-030 · O17 확정 · AC-086~088).

**이 기능의 가장 위험한 실패 양식을 막는 자리다.** 정찰에서 식별자를 기억으로 적었다가
틀렸는데 파이프라인은 오류를 내지 않고 매끄러운 설명을 돌려줬다 — 오사카성 자리에
**겨울 궁전**, 통천각 자리에 댈러스 카우보이스, 왓 프라깨오 자리에 시온수도회(실측).
설명이 비어 있으면 사용자는 없는 줄 알지만, **틀린 설명은 맞는 줄 안다.**

그래서 설명은 **좌표와 제목 두 신호**로 교차검증한 것만 남긴다. 판정표(§16.8 · O17)는
"둘 중 하나가 강하면 다른 하나를 완화하되, 둘 다 약하면 실패"다 — 너무 조이면 맞는 설명이
사라지고, 풀면 겨울 궁전이 통과한다.

**실패해도 장소는 남는다**(AC-088). 비우는 것은 설명뿐이고 그 스팟은 일정에 계속 배정된다.
목록에서 빼면 틀린 것을 고친 것이 아니라 **숨긴 것**이다.

순수 함수뿐이다 — 네트워크도 시계도 없다(AC-088 후단).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng

__all__ = [
    "COORD_LOOSE_M",
    "COORD_TIGHT_M",
    "LinkInput",
    "LinkVerdict",
    "apply_verdict",
    "label_match",
    "normalize_title",
    "verify_link",
]

# 라벨 근거가 약할 때(`partial`) 요구하는 거리.
COORD_TIGHT_M = 1_500.0
# 라벨이 정확히 일치할 때까지 허용하는 거리. 큰 공원·유적은 대표점이 이만큼 어긋난다.
COORD_LOOSE_M = 5_000.0

# 괄호 한정어: '에펠탑 (파리)' 의 괄호부. 반각·전각 모두 지운다.
_PARENTHETICAL = re.compile(r"[(（][^()（）]*[)）]")
# 공백과 가운뎃점류. 표기 차이만 남기고 지운다.
_SEPARATORS = re.compile(r"[\s·・･‧∙]")


@dataclass(frozen=True)
class LinkInput:
    """검증 한 건의 입력. **Q-id 는 좌표를 준 1단계 질의에서 나온 것만** 쓴다(§16.8 방어선 1).

    `page_coord` 가 `None` 인 것은 "문서에 좌표가 없다"이지 "0,0" 이 아니다 —
    인물·사건 문서가 그렇다. 둘을 같은 값으로 표현하면 대척점 거리 계산이 통과해 버린다.
    """

    spot_names: Sequence[str]
    spot_coord: LatLng
    page_title: str
    page_coord: LatLng | None = None
    has_description: bool = True
    redirected: bool = False


@dataclass(frozen=True)
class LinkVerdict:
    """판정 하나. 그대로 데이터셋의 `verification` 블록이 된다(AC-087)."""

    status: Literal["passed", "failed"]
    coord_delta_m: float | None
    label_match: Literal["exact", "partial", "none"]
    redirected: bool
    reason: str  # '' | 'coord_out_of_tolerance' | 'label_mismatch'
    #              | 'coords_missing_and_label_weak' | 'no_description'


def normalize_title(text: str) -> str:
    """NFKC → 소문자화 → 괄호 한정어 제거 → 공백·가운뎃점 제거 (§16.8).

    괄호를 지우는 이유와 **지우기만 하는 이유**가 같다: '워싱턴 (주)' 와 '워싱턴' 은 표기
    차이가 아니라 동명이의어다. 한정어를 지운 뒤에도 남는 본체가 같으면 `exact` 가 아니라
    같은 이름의 다른 대상일 수 있으므로, 최종 판정은 **좌표와 함께** 내린다.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _SEPARATORS.sub("", _PARENTHETICAL.sub("", folded)).strip()


def label_match(spot_names: Sequence[str], page_title: str) -> Literal["exact", "partial", "none"]:
    """스팟 이름들과 최종 문서 제목의 대조.

    `partial` 은 한쪽이 다른 쪽을 포함하는 경우다 — '한강 (다낭)' ↔ '한강교' 처럼 실제로
    같은 대상인데 표기가 늘어나는 일이 흔하다. 다만 **한 글자 포함은 보지 않는다**:
    '강' 하나로 아무 강이나 붙어 버린다.
    """
    title = normalize_title(page_title)
    if not title:
        return "none"
    names = [normalize_title(name) for name in spot_names]
    if any(name and name == title for name in names):
        return "exact"
    for name in names:
        if len(name) >= 2 and (name in title or title in name):
            return "partial"
    return "none"


def verify_link(spot: LinkInput) -> LinkVerdict:
    """§16.8 판정표를 **그 표 그대로** 적용한다.

    | 라벨 | ≤1,500m | ≤5,000m | >5,000m | 문서에 좌표 없음 |
    |------|---------|---------|---------|-----------------|
    | exact | passed | passed | failed | **passed** |
    | partial | passed | failed | failed | failed |
    | none | failed | failed | failed | failed |

    좌표 없는 문서라도 제목이 **정확히** 같으면 통과시킨다. 그 자리에 인물·사건 문서가
    들어오려면 제목까지 같아야 하는데, 그건 실패 양식이 아니라 동명이의어이고 괄호 한정어
    제거 규칙이 그런 제목을 `partial` 이하로 떨어뜨린다.
    """
    matched = label_match(spot.spot_names, spot.page_title)
    delta = None if spot.page_coord is None else haversine_m(spot.spot_coord, spot.page_coord)

    if not spot.has_description:
        # 설명 자체가 없다. 검증할 것이 없으므로 통과시키지 않는다 — '검증 표시 없는
        # 설명 보유 스팟 0건'(AC-087)은 이쪽도 표시를 남겨야 성립한다.
        return LinkVerdict("failed", delta, matched, spot.redirected, "no_description")

    if matched == "none":
        return LinkVerdict("failed", delta, matched, spot.redirected, "label_mismatch")

    if delta is None:
        if matched == "exact":
            return LinkVerdict("passed", None, matched, spot.redirected, "")
        return LinkVerdict("failed", None, matched, spot.redirected, "coords_missing_and_label_weak")

    tolerance = COORD_LOOSE_M if matched == "exact" else COORD_TIGHT_M
    if delta <= tolerance:
        return LinkVerdict("passed", delta, matched, spot.redirected, "")
    return LinkVerdict("failed", delta, matched, spot.redirected, "coord_out_of_tolerance")


def apply_verdict(spot: Mapping[str, Any], verdict: LinkVerdict) -> dict[str, Any]:
    """판정을 스팟에 적용한다 — **설명만 비우고 장소는 남긴다**(AC-088).

    실패 시 `description` 은 `""`, `description_source` 는 `None` 이 된다(AC-070 의 세 필드
    규칙: 셋 중 하나라도 없으면 설명이 비어 있어야 한다). 스팟 자신은 그대로 남아 일정에
    배정될 수 있고, 화면에는 "설명 미제공"이 뜬다.

    통과·실패 어느 쪽이든 `verification` 블록을 붙인다 — 그것이 AC-087 이 세는 표시다.
    """
    result = dict(spot)
    result["verification"] = {
        "status": verdict.status,
        "coord_delta_m": None if verdict.coord_delta_m is None else round(verdict.coord_delta_m, 1),
        "label_match": verdict.label_match,
        "redirected": verdict.redirected,
        "reason": verdict.reason,
    }
    if verdict.status == "failed":
        result["description"] = ""
        result["description_source"] = None
    return result
