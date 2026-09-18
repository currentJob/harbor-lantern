"""Behavioral checks for review weighting, fixed constraints and opening windows."""

from datetime import date

import pytest

from harbor_lantern.domain.guide import build_guide_plan
from harbor_lantern.domain.planner import build_plan
from harbor_lantern.domain.review_plan import propose_order, review_score


def evidence(rating, count):
    return {"status": "matched", "rating": rating, "review_count": count}


def test_small_review_sample_does_not_beat_reliable_rating():
    assert review_score(evidence(5, 1)) < review_score(evidence(4.6, 500))
    assert review_score({"status": "unmatched", "rating": 5, "review_count": 500}) is None


@pytest.mark.parametrize("rating,count", [(float("nan"), 10), (6, 10), (4, 0), (4, -1), (True, 10), (4, True)])
def test_invalid_reviews_have_no_score(rating, count):
    assert review_score(evidence(rating, count)) is None


def test_review_changes_selection_with_same_distance_and_hours():
    places = [{"id": name, "name": name, "lat": 22.3, "lng": 114.17, "category": "museum",
               "opening_hours": "24/7"} for name in ["a", "b", "c", "d"]]
    destination = {"name": "Hong Kong", "lat": 22.3, "lng": 114.17}
    start = date(2026, 10, 5)
    normal = build_plan(places, destination, start, start, "relaxed")
    assert normal["days"][0]["stops"][0]["place"]["id"] == "a"
    places[-1]["review"] = evidence(4.8, 500)
    reviewed = build_plan(places, destination, start, start, "relaxed")
    assert reviewed["days"][0]["stops"][0]["place"]["id"] == "d"


def test_swap_preserves_anchors_and_rejects_new_conflicts():
    spots = [{"id": i, "time_label": "오후"} for i in range(6)]

    def evaluate(order):
        return set(), sum(i*s["id"] for i, s in enumerate(order)), None

    order, _, _ = propose_order(spots, evaluate)
    assert order[0] == spots[0] and order[-1] == spots[-1]
    assert order != spots
    spots[2]["fixed_start_local"] = "15:00"
    spots[3]["time_label"] = "저녁"
    spots[4]["done"] = {"is_done": True}
    assert propose_order(spots, evaluate)[0] == spots
    for s in spots:
        s.pop("done", None)
        s.pop("fixed_start_local", None)
        s["time_label"] = "오후"

    def unsafe(order):
        return ({"closed"} if order != spots else set()), evaluate(order)[1], None

    assert propose_order(spots, unsafe)[0] == spots


def test_guide_waits_for_opening_and_does_not_visit_closed_place():
    city = {"city_id": "hong-kong", "name_ko": "홍콩", "center": {"lat": 22.3, "lng": 114.17}}
    spots = [{"id": str(i), "name": str(i), "lat": 22.3, "lng": 114.17,
              "category": "museum", "importance": {"sitelinks": 10-i}, "hours_text": hours}
             for i, hours in enumerate(["Mo off", "Mo 11:00-17:00", "Mo 09:00-09:30"])]
    day = date(2026, 10, 5)
    result = build_guide_plan(city, spots, day, day)
    stops = result["days"][0]["stops"]
    assert len(stops) == 1
    assert stops[0]["place"]["id"] == "1"
    assert stops[0]["arrival"] == "11:00"
    assert stops[0]["travel_minutes"] == 0
