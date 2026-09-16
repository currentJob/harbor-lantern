"""후보 정규화 — DSN-35 (§16.6 · REQ-022 · AC-065).

정찰이 잡은 중복의 두 경로를 그대로 재현한다: **도쿄 스미다강 2행 · 다낭 선짜산 3행**.
접히지 않으면 후보 수가 부풀고, 그 수치가 그대로 등급(REQ-029)의 입력이 된다 —
**세는 것이 틀리면 밝히는 것도 틀린다.**
"""

from __future__ import annotations

from harbor_lantern.domain.guide_harvest import (
    dedupe_by_name,
    fold_candidates,
    fold_rows,
)
from harbor_lantern.domain.models import LatLng

TOKYO = LatLng(35.6762, 139.6503)
DANANG = LatLng(16.0544, 108.2022)


def row(qid: str, lat: float, lng: float, sitelinks: int) -> dict:
    return {"qid": qid, "lat": lat, "lng": lng, "sitelinks": sitelinks}


def binding(qid: str, lat: float, lng: float, sitelinks: int) -> dict:
    """공급자 원문 모양 — SPARQL JSON 바인딩."""
    return {
        "item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"},
        "lat": {"type": "literal", "value": str(lat)},
        "lng": {"type": "literal", "value": str(lng)},
        "sitelinks": {"type": "literal", "value": str(sitelinks)},
    }


# ── 좌표 다중 문 접기 (정찰 실측) ─────────────────────────────────────────
def test_sumida_river_two_rows_fold_into_one() -> None:
    rows = [row("Q207864", 35.700, 139.800, 22), row("Q207864", 35.690, 139.790, 22)]
    candidates = fold_candidates(rows, TOKYO, 20_000.0)
    assert len(candidates) == 1
    assert candidates[0].coord_statements == 2


def test_son_tra_three_rows_fold_to_the_coordinate_nearest_the_center() -> None:
    """다낭 선짜산 3행 → 1건. 채택 좌표는 **중심 최근접**이다(`SAMPLE()` 이 아니다)."""
    rows = [
        row("Q3335330", 16.120, 108.280, 9),
        row("Q3335330", 16.100, 108.250, 9),  # ← 중심에 가장 가깝다
        row("Q3335330", 16.140, 108.300, 9),
    ]
    candidates = fold_candidates(rows, DANANG, 20_000.0)
    assert len(candidates) == 1
    assert candidates[0].coord == LatLng(16.100, 108.250)
    assert candidates[0].coord_statements == 3


def test_ties_between_equal_distances_break_on_lat_then_lng() -> None:
    """동률에도 한 값이 정해진다 — 정의되지 않은 선택은 재현성을 깬다(NFR-020)."""
    rows = [row("Q1", 35.6762, 139.6603, 5), row("Q1", 35.6762, 139.6403, 5)]
    folded = fold_candidates(rows, TOKYO, 20_000.0)
    assert folded[0].coord == LatLng(35.6762, 139.6403)


# ── 반경 재검증 (AC-065) ─────────────────────────────────────────────────
def test_a_candidate_outside_the_radius_is_dropped_with_a_reason() -> None:
    rows = [row("Q1", 35.6800, 139.6550, 30), row("Q2", 36.6000, 139.6500, 40)]
    result = fold_rows(rows, TOKYO, 6_000.0)
    assert [c.qid for c in result.candidates] == ["Q1"]
    assert [(d.qid, d.reason) for d in result.dropped] == [("Q2", "out_of_radius")]


def test_every_kept_candidate_is_inside_the_radius() -> None:
    """접기 뒤의 불변식 — 채택 좌표가 최근접이므로 이 검사가 항목 전체를 대변한다."""
    rows = [row("Q1", 35.700, 139.800, 10), row("Q1", 35.6765, 139.6510, 10)]
    for candidate in fold_candidates(rows, TOKYO, 3_000.0):
        assert candidate.distance_m <= 3_000.0


# ── 입력 형태·정렬·결정론 ────────────────────────────────────────────────
def test_sparql_bindings_and_flat_rows_give_the_same_candidates() -> None:
    flat = fold_candidates([row("Q243", 35.6800, 139.6550, 191)], TOKYO, 10_000.0)
    raw = fold_candidates([binding("Q243", 35.6800, 139.6550, 191)], TOKYO, 10_000.0)
    assert flat == raw


def test_malformed_rows_are_dropped_with_a_reason_not_an_exception() -> None:
    result = fold_rows([{"qid": "Q1", "lat": "", "lng": "", "sitelinks": "3"}], TOKYO, 10_000.0)
    assert result.candidates == ()
    assert result.dropped[0].reason == "malformed_row"


def test_order_is_sitelinks_desc_then_qid() -> None:
    """이 순서가 그대로 중요도 순위이고 AC-068 의 기준이다."""
    rows = [
        row("Q3", 35.677, 139.651, 20),
        row("Q1", 35.678, 139.652, 50),
        row("Q2", 35.679, 139.653, 20),
    ]
    assert [c.qid for c in fold_candidates(rows, TOKYO, 10_000.0)] == ["Q1", "Q2", "Q3"]


def test_input_order_does_not_change_the_output() -> None:
    rows = [
        row("Q3", 35.677, 139.651, 20),
        row("Q1", 35.678, 139.652, 50),
        row("Q1", 35.679, 139.653, 50),
    ]
    assert fold_candidates(rows, TOKYO, 10_000.0) == fold_candidates(list(reversed(rows)), TOKYO, 10_000.0)


# ── 이름 중복 (AC-065 "중복 이름 없음") ──────────────────────────────────
def test_duplicate_names_keep_the_larger_sitelinks_and_record_the_loser() -> None:
    spots = [
        {"wikidata_id": "Q1", "name": "한강", "importance": {"sitelinks": 13}},
        {"wikidata_id": "Q2", "name": "한강", "importance": {"sitelinks": 4}},
        {"wikidata_id": "Q3", "name": "롱교", "importance": {"sitelinks": 19}},
    ]
    result = dedupe_by_name(spots)
    assert [spot["wikidata_id"] for spot in result.kept] == ["Q3", "Q1"]
    assert [(d.qid, d.reason) for d in result.dropped] == [("Q2", "duplicate_name")]


def test_different_places_with_a_qualifier_are_not_merged() -> None:
    """'○○ 박물관'과 '○○ 박물관 (신관)' 은 다른 곳이다 — 더 센 정규화는 그것을 지운다."""
    spots = [
        {"wikidata_id": "Q1", "name": "국립박물관", "importance": {"sitelinks": 52}},
        {"wikidata_id": "Q2", "name": "국립박물관 (신관)", "importance": {"sitelinks": 7}},
    ]
    assert len(dedupe_by_name(spots).kept) == 2
