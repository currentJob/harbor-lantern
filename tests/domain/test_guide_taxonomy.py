"""방문 가능 분류기 — DSN-34 (§16.5 · REQ-023).

이 파일의 첫 번째 테스트가 이 모듈이 존재하는 이유다. 정찰에서 '스포츠 시설'을
하위분류까지 제외했더니 **로마에서 콜로세움이 사라졌다** — 오류도 경고도 없이, 목록에서
그냥 없어졌다. 그런 실패는 데이터를 눈으로 훑어야만 발견된다. 그래서 고정 입력으로 못박는다.
"""

from __future__ import annotations

from harbor_lantern.domain.guide_taxonomy import (
    DEFAULT_TAXONOMY,
    Taxonomy,
    classify,
)

# 정찰이 서술한 관계를 그대로 옮긴 고정 입력이다(원형경기장이 '스포츠 시설' 아래에 있다).
SPORTS_VENUE = "Q1076486"  # 스포츠 시설 — 제외 목록에 있다
AMPHITHEATRE = "Q641226"  # 원형경기장 — 그 하위분류
BUILDING = "Q41176"  # 건물 — allow_root
ROMA = Taxonomy(
    exclude_flat=frozenset({SPORTS_VENUE, "Q515"}),
    allow_flat={"Q33506": "museum"},
    allow_root={BUILDING: "building_landmark", "Q4989906": "monument"},
)
ROMA_ANCESTRY = {AMPHITHEATRE: [SPORTS_VENUE, BUILDING]}


# ── R7 · 콜로세움이 사라지지 않는다 ────────────────────────────────────────
def test_colosseum_survives_a_flat_exclusion_of_sports_venues() -> None:
    """직접 `P31` 이 '원형경기장'이면 제외되지 않는다 — 제외는 **평면**이다."""
    verdict = classify([AMPHITHEATRE], ROMA_ANCESTRY, ROMA)
    assert verdict.decision != "exclude"
    assert verdict.decision == "accept"
    assert verdict.root == "building_landmark"
    assert verdict.matched_class == BUILDING


def test_the_excluded_class_itself_is_still_excluded() -> None:
    """평면 제외가 무력해진 것이 아니다 — 직접 분류가 그것이면 여전히 제외다."""
    assert classify([SPORTS_VENUE], ROMA_ANCESTRY, ROMA).decision == "exclude"


def test_a_city_itself_is_excluded() -> None:
    """상위를 먹던 것들(도시 자신·언어·기업·사건·사람)은 평면 제외로 걸린다."""
    verdict = classify(["Q515"], {}, ROMA)
    assert (verdict.decision, verdict.reason, verdict.matched_class) == ("exclude", "excluded_flat", "Q515")


# ── 규칙 순서 (§16.5 표) ──────────────────────────────────────────────────
def test_exclusion_wins_over_allow_flat() -> None:
    """제외가 허용보다 먼저다. 순서가 규칙의 일부다."""
    assert classify(["Q33506", SPORTS_VENUE], {}, ROMA).decision == "exclude"
    assert classify([SPORTS_VENUE, "Q33506"], {}, ROMA).decision == "exclude"


def test_allow_flat_short_circuits_before_promotion() -> None:
    verdict = classify(["Q33506"], {"Q33506": [BUILDING]}, ROMA)
    assert (verdict.decision, verdict.reason, verdict.root) == ("accept", "allow_flat", "museum")


# ── 승급 (규칙 3) ─────────────────────────────────────────────────────────
def test_promotion_stops_after_three_levels() -> None:
    """4단계 위의 뿌리에는 닿지 않는다 — 더 올라가면 허용목록이 아무것도 거르지 않는다."""
    ancestry = {"A": ["B"], "B": ["C"], "C": ["D"], "D": [BUILDING]}
    assert classify(["A"], ancestry, ROMA).decision == "unclassified"
    assert classify(["B"], ancestry, ROMA).decision == "accept"  # 3단계면 닿는다


def test_promotion_takes_the_nearest_root() -> None:
    """가장 가까운 뿌리가 `category` 가 된다(너비 우선)."""
    ancestry = {"X": ["Q4989906", "Y"], "Y": [BUILDING]}
    assert classify(["X"], ancestry, ROMA).root == "monument"


def test_a_cycle_in_the_class_graph_terminates() -> None:
    """A ⊂ B ⊂ A 가 실제로 있다. 순회가 멈춰야 한다."""
    assert classify(["A"], {"A": ["B"], "B": ["A"]}, ROMA).decision == "unclassified"


def test_unknown_classes_are_recorded_not_silently_dropped() -> None:
    """규칙 4 — 버리되 **보고서에 남는다**. 그래서 `exclude` 가 아니라 `unclassified` 다."""
    verdict = classify(["Q999999999"], {}, ROMA)
    assert (verdict.decision, verdict.reason) == ("unclassified", "no_rule_matched")


def test_no_direct_classes_is_unclassified() -> None:
    assert classify([], {}, ROMA).decision == "unclassified"


# ── 결정론 (NFR-020) ─────────────────────────────────────────────────────
def test_same_input_gives_the_same_verdict() -> None:
    first = classify([AMPHITHEATRE], ROMA_ANCESTRY, ROMA)
    second = classify([AMPHITHEATRE], ROMA_ANCESTRY, ROMA)
    assert first == second


def test_default_taxonomy_is_usable_with_the_two_argument_contract() -> None:
    """설계서 §16.5 의 시그니처(`classify(direct, ancestry)`)가 그대로 동작한다."""
    assert classify(["Q33506"], {}).root == "museum"
    assert classify(["Q5"], {}).decision == "exclude"
    assert DEFAULT_TAXONOMY.allow_root["Q12280"] == "bridge"  # 다낭에서는 다리가 명소다


# ── 규칙 2-b — 허용 뿌리 자신 (다낭 사건) ────────────────────────────────────
def test_a_direct_class_that_is_itself_an_allow_root_is_accepted() -> None:
    """`P31` 이 허용 뿌리 **자신**이면 받아들인다.

    승급(규칙 3)은 부모부터 훑기 때문에 이 자리를 보지 않았다. 그 결과 `P31` 이
    '다리'·'강'인 항목이 어떤 규칙에도 안 걸려 `unclassified` 로 빠졌다 —
    **다낭 수확 12건 중 다리 4·강 2 가 통째로 사라지는 경로**다(정찰 실측 2026-09-15).
    조상 캐시가 비어 있어도(= 그 클래스의 P279 를 조회한 적이 없어도) 걸려야 한다.
    """
    for qid, expected_root in (("Q12280", "bridge"), ("Q4022", "river"),
                               ("Q22698", "park"), ("Q41176", "building_landmark")):
        result = classify([qid], {}, DEFAULT_TAXONOMY)
        assert result.decision == "accept", f"{qid} 가 {result.decision} 로 빠졌다"
        assert result.root == expected_root
        assert result.matched_class == qid
        assert result.reason == "allow_root_self"


def test_allow_root_self_does_not_outrank_a_flat_exclusion() -> None:
    """규칙 순서는 그대로다 — 제외가 먼저다.

    규칙 2-b 를 규칙 1 앞에 두면 제외 목록이 조용히 무력해진다.
    """
    taxonomy = Taxonomy(
        exclude_flat=frozenset({"Q12280"}),
        allow_flat={},
        allow_root={"Q12280": "bridge"},
    )
    assert classify(["Q12280"], {}, taxonomy).decision == "exclude"


def test_every_default_allow_root_is_reachable_without_an_ancestry_cache() -> None:
    """기본표의 뿌리가 **하나도 빠짐없이** 조상 캐시 없이 판정된다.

    `allow_root` 에만 있고 `allow_flat` 에 없는 QID 가 7 개였고, 그 전부가
    `unclassified` 였다. 목록을 손으로 맞추는 대신 이 불변식을 검사로 둔다 —
    앞으로 뿌리를 추가하는 사람은 아무것도 기억하지 않아도 된다.
    """
    unreachable = [
        qid for qid in DEFAULT_TAXONOMY.allow_root
        if classify([qid], {}, DEFAULT_TAXONOMY).decision != "accept"
    ]
    assert not unreachable, f"조상 캐시 없이 분류되지 않는 뿌리: {unreachable}"
