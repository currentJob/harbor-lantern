"""방문 가능 분류기 — DSN-34 (설계서 §16.5 · REQ-023 · O11 확정).

**순수 함수다.** 위키데이터 응답의 `P31`(직접 분류) 목록과 `P279`(상위분류) 캐시를 받아
"이것이 방문 대상인가"를 판정한다. 네트워크도 파일도 시계도 없다 — 분류는 이 기능에서
가장 자주 틀리고 가장 자주 고칠 규칙이라(R6 · R7), 고정 입력으로 테스트되는 자리에 둔다.

**제외는 평면 `P31` 로만 한다(R7 · 절대 규칙).** 하위분류(`P279*`)까지 제외했더니
**로마에서 콜로세움이 사라졌다** — 원형경기장이 '스포츠 시설' 아래에 달려 있다(정찰 실측).
평면으로 바꾸자 돌아왔다. 그래서 규칙 1 은 상위를 **한 단계도** 올라가지 않는다.

**승급(규칙 3)은 방향이 반대라 안전하다.** 제외의 `P279*` 는 위에서 아래로 훑어 예상 못 한
자손을 삼키지만, 승급은 아래에서 위로 `MAX_PROMOTION_DEPTH` 단계만 올라가 **명시된 뿌리에
닿는지**만 본다. 닿지 못하면 `accept` 가 아니라 `unclassified` 이므로 침묵 삭제가 아니라
**기록된 보류**이고, 다음 굽기에서 사람이 허용목록에 올릴 수 있다.

허용·제외 목록의 SSoT 는 커밋되는 `tools/wikidata-classes.json` 이다(굽기가 읽어 넘긴다).
여기 있는 `DEFAULT_TAXONOMY` 는 설계서 §16.5 가 **본문에 열거한 것만** 담은 기본값이며,
그 파일을 대신하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "DEFAULT_TAXONOMY",
    "MAX_PROMOTION_DEPTH",
    "Classification",
    "Taxonomy",
    "classify",
]

# 승급으로 올라가는 최대 단계(§16.5 규칙 3). 더 올리면 '실체'·'물리적 객체' 같은
# 최상위 개념에 무엇이든 닿는다 — 그때부터 허용목록은 아무것도 거르지 않는다.
MAX_PROMOTION_DEPTH = 3


@dataclass(frozen=True)
class Taxonomy:
    """허용·제외 분류표. `tools/wikidata-classes.json` 의 세 블록과 같은 모양이다.

    `allow_root` 만 값(= `root` 라벨)을 갖는다. 그 값이 스팟의 `category` 가 되고
    화면 라벨과 저녁 슬롯 규칙의 입력이 된다(§16.13 · §16.14).
    """

    exclude_flat: frozenset[str] = frozenset()
    allow_flat: Mapping[str, str] = field(default_factory=dict)
    allow_root: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Classification:
    """판정 하나. `decision` 이 `unclassified` 여도 **버려지되 보고서에 남는다**(§16.5 규칙 4)."""

    decision: Literal["accept", "exclude", "unclassified"]
    root: str | None
    matched_class: str | None
    reason: str


# 설계서 §16.5 의 jsonc 예시에 **적혀 있는 것만** 옮긴 기본표다. 실제 굽기는
# `tools/wikidata-classes.json` 을 읽어 `taxonomy=` 로 넘긴다(그쪽이 SSoT).
DEFAULT_TAXONOMY = Taxonomy(
    exclude_flat=frozenset({"Q515", "Q34770", "Q4830453", "Q178561", "Q5"}),
    allow_flat={"Q33506": "museum", "Q570116": "attraction", "Q16970": "worship"},
    allow_root={
        "Q570116": "attraction",
        "Q33506": "museum",
        "Q1370598": "worship",
        "Q23413": "castle",
        "Q22698": "park",
        "Q4989906": "monument",
        "Q41176": "building_landmark",
        "Q12280": "bridge",
        "Q4022": "river",
    },
)


def classify(
    direct_classes: Sequence[str],
    ancestry: Mapping[str, Sequence[str]],
    taxonomy: Taxonomy = DEFAULT_TAXONOMY,
) -> Classification:
    """직접 `P31` 과 `P279` 캐시로 방문 가능 여부를 판정한다 (§16.5 의 네 규칙, **그 순서로**).

    `ancestry` 는 `클래스 QID → 상위 QID 들` 이다. 굽기 중 `wbgetentities` 로 채워져
    커밋되므로(§16.5), **같은 입력에 같은 판정**이 나온다(NFR-020).

    같은 순위의 규칙 안에서는 `direct_classes` 에 적힌 순서대로 본다 — 어느 클래스가
    판정을 냈는지(`matched_class`)가 사후 감사의 단서이므로 순서가 흔들리면 안 된다.
    """
    for qid in direct_classes:
        if qid in taxonomy.exclude_flat:
            return Classification("exclude", None, qid, "excluded_flat")

    for qid in direct_classes:
        root = taxonomy.allow_flat.get(qid)
        if root is not None:
            return Classification("accept", root, qid, "allow_flat")

    promoted = _promote(direct_classes, ancestry, taxonomy)
    if promoted is not None:
        return promoted

    return Classification("unclassified", None, None, "no_rule_matched")


def _promote(
    direct_classes: Sequence[str],
    ancestry: Mapping[str, Sequence[str]],
    taxonomy: Taxonomy,
) -> Classification | None:
    """`P279` 를 최대 `MAX_PROMOTION_DEPTH` 단계 올라가 `allow_root` 에 닿는지 본다.

    너비 우선이다 — **가장 가까운 뿌리**가 그 스팟의 분류여야 하기 때문이다. 깊이 우선이면
    한 가지를 끝까지 올라가다 만난 먼 뿌리가 이기고, `category` 가 입력 순서에 흔들린다.
    `seen` 이 순환(A ⊂ B ⊂ A)을 끊는다 — 위키데이터 클래스 그래프에 실제로 있는 형태다.
    """
    frontier = list(direct_classes)
    seen = set(frontier)
    for _ in range(MAX_PROMOTION_DEPTH):
        parents: list[tuple[str, str]] = []  # (부모 QID, 출발한 직접 클래스)
        for qid in frontier:
            for parent in ancestry.get(qid, ()):  # 캐시에 없으면 그 가지는 거기서 끝난다
                if parent in seen:
                    continue
                seen.add(parent)
                parents.append((parent, qid))
        for parent, _source in parents:
            root = taxonomy.allow_root.get(parent)
            if root is not None:
                return Classification("accept", root, parent, "promoted_to_root")
        if not parents:
            return None
        frontier = [parent for parent, _source in parents]
    return None
