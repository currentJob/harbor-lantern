"""가이드 기반 일정 생성 — DSN-43 (§16.14 · AC-066~068 · AC-073 · AC-074 · AC-085).

AC-068 이 이 파일의 중심이다 — **파리 3일 일정에 에펠탑·루브르·노트르담이 모두 들어간다.**
현재(폴백) 구현에서는 셋 다 후보에조차 없었다(P13). 하루를 한 지역으로 묶는 규칙(AC-073)과
상충하면 **상위 포함이 이기고**, 그 사실이 응답에 표시된다.

이동시간은 기존 플래너와 **같은 식**을 쓴다. 두 경로가 다른 이동시간을 말하면 같은 화면에서
사용자가 그것을 본다 — 그래서 `travel_minutes` 추출의 회귀 검사도 여기 있다.
"""

from __future__ import annotations

from datetime import date

import pytest

from harbor_lantern.domain.guide import build_guide_plan
from harbor_lantern.domain.planner import travel_minutes

PARIS = {
    "city_id": "paris",
    "name_ko": "파리",
    "center": {"lat": 48.8566, "lng": 2.3522},
    "grade": "full",
    "retrieved_at": "2026-09-15",
    "sources": [{"what": "Wikidata", "url": "https://www.wikidata.org/", "retrieved_at": "2026-09-15"}],
    "known_gaps": ["설명이 없는 스팟이 있다."],
}

# sitelinks 는 설계서 §16.14-4 가 인용한 값(에펠탑 191 · 루브르 169 · 노트르담 125)을 쓰고,
# 나머지는 순위를 만들기 위한 고정 입력이다.
def spot(sid, name, lat, lng, category, area, sitelinks, **extra) -> dict:
    base = {
        "id": f"wd:{sid}",
        "wikidata_id": sid,
        "name": name,
        "name_source": "ko",
        "lat": lat,
        "lng": lng,
        "category": category,
        "area": area,
        "importance": {"sitelinks": sitelinks},
        "hours_text": "",
        "evening_candidate": category in ("viewpoint", "tower", "observation"),
    }
    base.update(extra)
    return base


PARIS_SPOTS = [
    spot("Q243", "에펠탑", 48.8584, 2.2945, "monument", "7구", 191),
    spot("Q19675", "루브르 박물관", 48.8606, 2.3376, "museum", "1구", 169),
    spot("Q2981", "노트르담 대성당", 48.8530, 2.3499, "worship", "4구", 125),
    spot("Q64436", "개선문", 48.8738, 2.2950, "monument", "8구", 100),
    spot("Q23402", "오르세 미술관", 48.8600, 2.3266, "museum", "7구", 95),
    spot("Q188507", "사크레쾨르 대성당", 48.8867, 2.3431, "worship", "18구", 90),
    spot("Q131013", "팡테옹", 48.8462, 2.3464, "monument", "5구", 85),
    spot("Q843878", "뤽상부르 공원", 48.8462, 2.3372, "park", "6구", 80),
    spot("Q184123", "퐁피두 센터", 48.8607, 2.3522, "museum", "4구", 75),
    spot("Q590794", "몽파르나스 타워", 48.8422, 2.3220, "viewpoint", "15구", 70),
    spot("Q193089", "생트샤펠", 48.8554, 2.3450, "worship", "1구", 65),
    spot("Q262271", "알렉상드르 3세 다리", 48.8639, 2.3135, "bridge", "8구", 60),
    spot("Q209043", "방돔 광장", 48.8675, 2.3295, "monument", "1구", 55),
    spot("Q207148", "튈르리 정원", 48.8635, 2.3270, "park", "1구", 50),
    spot("Q200297", "오페라 가르니에", 48.8720, 2.3316, "monument", "9구", 45),
]

START, END = date(2026, 10, 5), date(2026, 10, 7)  # 3일


def scheduled_ids(plan: dict) -> list[str]:
    return [stop["guide_id"] for day in plan["days"] for stop in day["stops"]]


# ── AC-068 · 상위 스팟이 들어간다 ─────────────────────────────────────────
def test_paris_three_days_contains_the_eiffel_tower_the_louvre_and_notre_dame() -> None:
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    ids = scheduled_ids(plan)
    assert {"wd:Q243", "wd:Q19675", "wd:Q2981"} <= set(ids)


def test_seven_of_the_top_ten_are_scheduled_in_a_full_grade_city() -> None:
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    top_ten = {spot_["id"] for spot_ in PARIS_SPOTS[:10]}
    assert len(top_ten & set(scheduled_ids(plan))) >= 7


def test_the_reservation_survives_an_interest_filter() -> None:
    """관심사 필터는 예약된 상위 스팟에 적용하지 않는다 — AC-068 이 우선한다."""
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END, interests="culture")
    ids = set(scheduled_ids(plan))
    assert "wd:Q843878" in ids  # 뤽상부르 공원은 상위 10 안이라 남는다
    assert "wd:Q207148" not in ids  # 튈르리 정원(비예약 · park)은 걸러진다


# ── AC-066 · AC-067 ──────────────────────────────────────────────────────
def test_every_stop_comes_from_the_baked_guide_and_carries_its_identifier() -> None:
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    known = {spot_["id"] for spot_ in PARIS_SPOTS}
    stops = [stop for day in plan["days"] for stop in day["stops"]]
    assert stops
    for stop in stops:
        assert stop["guide_id"] in known
        assert stop["place"]["name"]


def test_the_same_request_twice_gives_the_same_plan() -> None:
    first = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    second = build_guide_plan(PARIS, list(reversed(PARIS_SPOTS)), START, END)
    assert first == second


def test_no_spot_is_scheduled_twice_across_the_trip() -> None:
    ids = scheduled_ids(build_guide_plan(PARIS, PARIS_SPOTS, START, END))
    assert len(ids) == len(set(ids))


# ── AC-073 · 하루의 제목·지역·색 ──────────────────────────────────────────
def test_each_day_carries_a_title_an_area_and_a_color() -> None:
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    assert len(plan["days"]) == 3
    for day in plan["days"]:
        assert day["title"]
        assert day["area"]
        assert day["color"].startswith("#")
        assert isinstance(day["area_exception"], bool)


def test_a_day_that_borrows_a_spot_from_another_area_says_so() -> None:
    """조용히 섞지 않는다 — 다른 지역 스팟이 들어가면 사유가 응답에 실린다."""
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    for day in plan["days"]:
        if day["area_exception"]:
            assert day["area_exception_reason"]
        else:
            assert day["area_exception_reason"] == ""


def test_the_exception_message_names_the_spots_it_is_about() -> None:
    """사유가 "일부 다름" 으로만 적히면 사용자는 어느 것이 다른지 알 수 없다."""
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    flagged = [day for day in plan["days"] if day["area_exception"]]
    assert flagged
    for day in flagged:
        outside = [stop["place"]["name"] for stop in day["stops"] if stop["place"]["area"] != day["area"]]
        assert outside
        for name in outside:
            assert name in day["area_exception_reason"]


# ── AC-074 · 저녁 슬롯 ───────────────────────────────────────────────────
EVENING_CITY = {"city_id": "x", "name_ko": "가상", "center": {"lat": 0.0, "lng": 0.0}, "grade": "partial"}
DAY = date(2026, 10, 5)


def evening_fixture(hours_text: str) -> list[dict]:
    return [
        spot("Q1", "박물관", 0.010, 0.000, "museum", "가구", 50),
        spot("Q2", "사원", 0.012, 0.002, "worship", "가구", 40),
        spot("Q3", "전망대", 0.014, 0.004, "viewpoint", "가구", 30, hours_text=hours_text),
    ]


def test_a_viewpoint_takes_the_last_slot_of_the_day_after_1900() -> None:
    plan = build_guide_plan(EVENING_CITY, evening_fixture(""), DAY, DAY, pace="relaxed")
    stops = plan["days"][0]["stops"]
    assert stops[-1]["guide_id"] == "wd:Q3"
    assert stops[-1]["evening_slot"] is True
    assert stops[-1]["arrival"] >= "19:00"


def test_a_spot_confirmed_closed_in_the_evening_does_not_take_the_slot() -> None:
    plan = build_guide_plan(EVENING_CITY, evening_fixture("Mo-Su 09:00-17:00"), DAY, DAY, pace="relaxed")
    stops = plan["days"][0]["stops"]
    assert not any(stop["evening_slot"] for stop in stops)
    assert "wd:Q3" in [stop["guide_id"] for stop in stops]  # 저녁이 아닐 뿐 일정에는 남는다


def test_unreadable_hours_are_not_treated_as_closed() -> None:
    """못 읽은 것을 근거로 배제하면 거짓 경고를 만드는 것과 같다(R1 · AC-024)."""
    plan = build_guide_plan(EVENING_CITY, evening_fixture("summer: dusk-dawn"), DAY, DAY, pace="relaxed")
    stops = plan["days"][0]["stops"]
    assert stops[-1]["guide_id"] == "wd:Q3"
    assert stops[-1]["hours_status"] == "unverified"


def test_hours_status_is_reported_like_the_fallback_planner() -> None:
    plan = build_guide_plan(EVENING_CITY, evening_fixture("Mo-Su 09:00-22:00"), DAY, DAY, pace="relaxed")
    statuses = {stop["hours_status"] for stop in plan["days"][0]["stops"]}
    assert statuses <= {"weekly_hours", "unverified"}
    assert "weekly_hours" in statuses


# ── 하루 창·뼈대·등급 표시 ────────────────────────────────────────────────
def test_nothing_is_scheduled_past_the_day_window() -> None:
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    for day in plan["days"]:
        for stop in day["stops"]:
            assert "09:00" <= stop["arrival"] <= "21:00"
            assert stop["departure"] <= "21:00"


def test_the_response_keeps_the_fallback_skeleton() -> None:
    """화면 렌더러를 둘로 가르지 않기 위해 기존 `build_plan` 과 같은 키를 쓴다."""
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    assert {"destination", "start_date", "end_date", "days", "algorithm", "notice",
            "candidate_count", "scheduled_count"} <= set(plan)
    first = plan["days"][0]["stops"][0]
    assert {"place", "arrival", "departure", "travel_minutes", "distance_m", "hours_status"} <= set(first)
    assert (first["travel_minutes"], first["distance_m"]) == (0, 0)  # 첫 스팟까지의 이동은 세지 않는다


def test_the_grade_is_always_present() -> None:
    """AC-085 — 비어 있으면 화면이 아무 문구도 고르지 못한다."""
    assert build_guide_plan(PARIS, PARIS_SPOTS, START, END)["guide_grade"] == "full"
    partial = build_guide_plan(dict(PARIS, grade="partial"), PARIS_SPOTS, START, END)
    assert partial["guide_grade"] == "partial"


def test_sources_and_retrieval_date_ride_along() -> None:
    plan = build_guide_plan(PARIS, PARIS_SPOTS, START, END)
    assert plan["guide_source"]["retrieved_at"] == "2026-09-15"
    assert plan["guide_source"]["sources"][0]["url"].startswith("https://")
    assert plan["guide_source"]["known_gaps"]


def test_a_city_without_spots_still_returns_days() -> None:
    plan = build_guide_plan(PARIS, [], START, END)
    assert [day["stops"] for day in plan["days"]] == [[], [], []]
    assert plan["scheduled_count"] == 0


def test_pace_sets_the_number_of_slots() -> None:
    relaxed = build_guide_plan(PARIS, PARIS_SPOTS, START, END, pace="relaxed")
    assert all(len(day["stops"]) <= 3 for day in relaxed["days"])


# ── `travel_minutes` 추출 회귀 (§16.14) ──────────────────────────────────
@pytest.mark.parametrize("distance", [0.0, 1.0, 40.0, 79.0, 1199.9, 1200.0, 1200.1, 3000.0, 12345.6])
def test_travel_minutes_matches_the_formula_it_was_extracted_from(distance: float) -> None:
    """**값이 바뀌면 안 된다.** 추출 전의 인라인 식을 그대로 다시 적어 대조한다."""
    expected = max(1, round(distance * 1.35 / 80)) if distance <= 1200 else round(distance * 1.45 / 366) + 8
    assert travel_minutes(distance) == expected
