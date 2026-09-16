"""선별과 등급 판정 — DSN-49 · DSN-38 (§16.9 · REQ-029 · AC-083).

표본 검산은 설계서 §16.9.2 의 표를 그대로 옮긴 것이다 — 도쿄 완전 · 다낭 부분 ·
나트랑/푸꾸옥 미달 · **베네치아 완전**. 마지막 하나가 DSN-49 의 이유다: 같은 도시가
후보 전체로 재면 미달 쪽으로, 상위 25건으로 재면 완전으로 간다.
"""

from __future__ import annotations

from harbor_lantern.domain.guide_grade import (
    EXPORT_MAX,
    GRADE_WINDOW,
    grade,
    grade_city,
    select_spots,
)


def spots(count: int, ko: int, *, start: int = 0) -> list[dict]:
    """`count` 건 중 앞 `ko` 건이 한국어 라벨. `sitelinks` 는 내림차순으로 준다."""
    return [
        {
            "id": f"wd:Q{start + i:04}",
            "wikidata_id": f"Q{start + i:04}",
            "name_source": "ko" if i < ko else "en",
            "importance": {"sitelinks": 1000 - (start + i)},
        }
        for i in range(count)
    ]


# ── AC-083 표본 검산 (§16.9.2 표) ────────────────────────────────────────
def test_tokyo_is_full() -> None:
    """32건 중 한국어 30 — 한국어가 아닌 둘이 **상위 25 안에 있어도** 완전이다."""
    selected = spots(32, 32)
    selected[3]["name_source"] = "en"
    selected[7]["name_source"] = "native"
    city = grade_city(selected)
    assert city.grade == "full"
    assert city.spot_count == 32  # 개수는 꼬리까지 센다
    assert city.grade_window == GRADE_WINDOW  # 비율은 쓰는 자리에서만 센다
    assert (city.ko_label_count, city.ko_label_ratio) == (23, 0.92)


def test_danang_is_partial_because_of_the_spot_floor() -> None:
    city = grade_city(spots(12, 10))
    assert city.grade == "partial"
    assert (city.spot_count, city.ko_label_count, city.grade_window) == (12, 10, 12)
    assert city.ko_label_ratio == 0.833


def test_nha_trang_is_below_on_count_alone() -> None:
    """라벨 80% 여도 5건이면 미달이다 — 일정을 채울 수 없다."""
    city = grade_city(spots(5, 4))
    assert (city.grade, city.ko_label_ratio) == ("below", 0.8)


def test_phu_quoc_is_below_on_both() -> None:
    assert grade_city(spots(3, 0)).grade == "below"


# ── DSN-49 · 베네치아가 뒤집히는 자리 ────────────────────────────────────
def test_venice_is_full_when_measured_on_the_selected_spots() -> None:
    """후보 80건 기준 35% 지만 상위 25건 기준 84% 다 — 꼬리는 일정에 한 번도 쓰이지 않는다."""
    candidates = spots(25, 21) + spots(55, 7, start=100)  # 상위 25 중 21 · 전체 80 중 28(35%)
    city = grade_city(select_spots(candidates))
    assert city.ko_label_ratio == 0.84
    assert city.grade == "full"
    assert city.spot_count == EXPORT_MAX  # 수록 상한까지만 선별된다


def test_measuring_on_the_whole_candidate_set_would_have_dropped_venice() -> None:
    """같은 도시를 후보 전체 비율로 재면 미달로 간다. **재는 자리가 판정을 바꾼다.**"""
    assert grade(80, 0.35) == "below"


def test_bali_stays_below_even_after_selection() -> None:
    """선별로 가려지지 않는 진짜 미달 — 개수가 아니라 언어가 이유다."""
    city = grade_city(spots(25, 5))
    assert (city.grade, city.ko_label_ratio) == ("below", 0.2)


# ── 판정 규칙 자체 (AC-083 문언) ─────────────────────────────────────────
def test_grade_thresholds() -> None:
    assert grade(15, 0.80) == "full"
    assert grade(14, 0.95) == "partial"  # 스팟 하한에 걸린다
    assert grade(20, 0.79) == "partial"
    assert grade(8, 0.50) == "partial"  # 경계는 미달이 아니다
    assert grade(7, 1.00) == "below"
    assert grade(20, 0.49) == "below"
    assert grade(0, 0.0) == "below"


def test_only_three_grades_exist() -> None:
    """'완전−' 같은 중간 등급을 만들지 않는다 — 화면 문구가 넷이 되면 구분되지 않는다."""
    values = {grade(count, ratio) for count in range(0, 40, 3) for ratio in (0.0, 0.3, 0.6, 0.85, 1.0)}
    assert values <= {"full", "partial", "below"}


def test_recomputing_from_the_recorded_metrics_reproduces_the_recorded_grade() -> None:
    """AC-083 후단 — 지표만 비교하지 않는다. 지표 자체가 잘못 계산됐을 수 있다."""
    for selected in (spots(32, 30), spots(12, 10), spots(5, 4), spots(25, 5)):
        city = grade_city(selected)
        assert grade(city.spot_count, city.ko_label_ratio) == city.grade


def test_fallback_named_spots_are_not_counted_as_korean() -> None:
    """폴백으로 채운 이름(영어·원어)을 한국어로 세면 비율이 스스로를 증명하게 된다."""
    selected = [dict(spot, name_source="native") for spot in spots(20, 20)]
    assert grade_city(selected).ko_label_count == 0


# ── 선별 (DSN-49) ────────────────────────────────────────────────────────
def test_selection_order_is_sitelinks_desc_then_qid() -> None:
    unordered = [
        {"id": "wd:Q3", "wikidata_id": "Q3", "importance": {"sitelinks": 10}},
        {"id": "wd:Q1", "wikidata_id": "Q1", "importance": {"sitelinks": 50}},
        {"id": "wd:Q2", "wikidata_id": "Q2", "importance": {"sitelinks": 10}},
    ]
    assert [spot["wikidata_id"] for spot in select_spots(unordered)] == ["Q1", "Q2", "Q3"]


def test_selection_is_capped_at_the_export_limit() -> None:
    assert len(select_spots(spots(120, 120))) == EXPORT_MAX


def test_an_empty_city_is_below_and_does_not_divide_by_zero() -> None:
    city = grade_city([])
    assert (city.grade, city.ko_label_ratio, city.grade_window) == ("below", 0.0, 0)
