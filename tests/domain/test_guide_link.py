"""연결 검증 — DSN-37 (§16.8 · REQ-030 · AC-086~088).

**겨울 궁전 테스트가 이 파일의 이유다.** 오사카성 좌표·이름에 겨울 궁전 문서를 붙인
고정 입력을 주면 설명이 비워져야 한다(AC-086). 설명이 채워지면 이 테스트가 실패한다 —
그것이 정찰에서 실제로 일어난 일이고, 파이프라인은 오류 하나 내지 않았다.
"""

from __future__ import annotations

import pytest

from harbor_lantern.domain.guide_link import (
    LinkInput,
    apply_verdict,
    label_match,
    normalize_title,
    verify_link,
)
from harbor_lantern.domain.models import LatLng

OSAKA_CASTLE = LatLng(34.6873, 135.5259)
WINTER_PALACE = LatLng(59.9398, 30.3146)  # 상트페테르부르크 — 약 8,000km 떨어져 있다


# ── AC-086 · 겨울 궁전 ────────────────────────────────────────────────────
def test_the_winter_palace_does_not_pass_as_osaka_castle() -> None:
    verdict = verify_link(LinkInput(
        spot_names=["오사카성", "大阪城", "Osaka Castle"],
        spot_coord=OSAKA_CASTLE,
        page_title="겨울 궁전",
        page_coord=WINTER_PALACE,
    ))
    assert verdict.status == "failed"
    assert verdict.label_match == "none"
    assert verdict.reason == "label_mismatch"
    assert verdict.coord_delta_m is not None and verdict.coord_delta_m > 5_000.0


def test_a_failed_link_empties_the_description_but_keeps_the_place() -> None:
    """AC-088 — 목록에서 빼면 틀린 것을 고친 것이 아니라 숨긴 것이다."""
    spot = {
        "id": "wd:Q182147",
        "name": "오사카성",
        "lat": OSAKA_CASTLE.lat,
        "lng": OSAKA_CASTLE.lng,
        "description": "겨울 궁전은 상트페테르부르크에 있는…",
        "description_source": {"url": "https://ko.wikipedia.org/wiki/겨울_궁전"},
    }
    verdict = verify_link(LinkInput(["오사카성"], OSAKA_CASTLE, "겨울 궁전", WINTER_PALACE))
    result = apply_verdict(spot, verdict)
    assert result["description"] == ""
    assert result["description_source"] is None
    assert result["id"] == "wd:Q182147"  # 장소는 남는다
    assert result["lat"] == OSAKA_CASTLE.lat
    assert result["verification"]["status"] == "failed"  # AC-087
    assert result["verification"]["reason"] == "label_mismatch"


def test_a_passing_link_keeps_the_description_and_records_the_delta() -> None:
    """AC-087 — 설명을 가진 스팟은 검증 표시와 좌표 오차 값을 갖는다."""
    spot = {"id": "wd:Q243", "name": "에펠탑", "description": "…", "description_source": {"url": "…"}}
    verdict = verify_link(LinkInput(["에펠탑"], LatLng(48.8584, 2.2945), "에펠탑", LatLng(48.8583, 2.2944)))
    result = apply_verdict(spot, verdict)
    assert result["description"] == "…"
    assert result["verification"]["status"] == "passed"
    assert result["verification"]["coord_delta_m"] is not None


# ── 판정표 (O17 · §16.8) ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("names", "title", "delta_deg", "expected"),
    [
        (["에펠탑"], "에펠탑", 0.01, "passed"),  # exact · 약 1.1km
        (["에펠탑"], "에펠탑", 0.03, "passed"),  # exact · 약 3.3km (≤5,000m)
        (["에펠탑"], "에펠탑", 0.08, "failed"),  # exact · 약 8.9km
        (["한강"], "한강교", 0.005, "passed"),  # partial · 약 550m
        (["한강"], "한강교", 0.03, "failed"),  # partial · 1,500m 초과
        (["통천각"], "댈러스 카우보이스", 0.001, "failed"),  # none · 거리와 무관
    ],
)
def test_the_decision_table_is_applied_as_written(names, title, delta_deg, expected) -> None:
    base = LatLng(48.8584, 2.2945)
    verdict = verify_link(LinkInput(names, base, title, LatLng(base.lat + delta_deg, base.lng)))
    assert verdict.status == expected


def test_a_page_without_coordinates_passes_only_on_an_exact_title() -> None:
    exact = verify_link(LinkInput(["센소지"], LatLng(35.7148, 139.7967), "센소지", None))
    assert (exact.status, exact.coord_delta_m) == ("passed", None)

    weak = verify_link(LinkInput(["센소지"], LatLng(35.7148, 139.7967), "센소지 경내", None))
    assert (weak.status, weak.reason) == ("failed", "coords_missing_and_label_weak")


def test_a_spot_without_a_description_is_recorded_not_passed() -> None:
    verdict = verify_link(LinkInput(["에펠탑"], LatLng(48.8584, 2.2945), "에펠탑", None, has_description=False))
    assert (verdict.status, verdict.reason) == ("failed", "no_description")


def test_a_redirect_is_recorded_but_is_not_a_failure() -> None:
    """리디렉트 자체는 흔하다. 다만 제목이 조용히 다른 주제로 옮겨 가는 경로라 기록한다."""
    verdict = verify_link(LinkInput(
        ["에펠탑"], LatLng(48.8584, 2.2945), "에펠탑", LatLng(48.8584, 2.2945), redirected=True,
    ))
    assert (verdict.status, verdict.redirected) == ("passed", True)


# ── 제목 정규화·라벨 대조 ────────────────────────────────────────────────
def test_normalize_title_folds_width_case_parentheses_and_separators() -> None:
    assert normalize_title("ＴＯＫＹＯ Tower") == "tokyotower"
    assert normalize_title("에펠탑 (파리)") == "에펠탑"
    assert normalize_title("산 마르코 · 대성당") == "산마르코대성당"


def test_label_match_levels() -> None:
    assert label_match(["에펠탑", "Tour Eiffel"], "Tour Eiffel") == "exact"
    assert label_match(["한강"], "한강교") == "partial"
    assert label_match(["오사카성"], "겨울 궁전") == "none"


def test_a_single_character_name_does_not_partially_match_everything() -> None:
    """'강' 하나로 아무 강이나 붙는 것을 막는다."""
    assert label_match(["강"], "한강교") == "none"


def test_an_empty_page_title_never_matches() -> None:
    assert label_match(["에펠탑"], "") == "none"
    assert verify_link(LinkInput(["에펠탑"], LatLng(48.8584, 2.2945), "", LatLng(48.8584, 2.2945))).status == "failed"
