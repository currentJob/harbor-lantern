"""Calendar, time-window and itinerary invariants."""

from datetime import date

import pytest

from harbor_lantern.domain.planner import build_plan, opening_windows


@pytest.mark.parametrize(("text", "day", "expected"), [
    ("24/7", 0, [(0, 1440)]), ("Mo-Fr 09:00-17:00", 6, []),
    ("Mo-Fr 09:00-17:00; Sa 10:00-13:00; Su off", 5, [(600, 780)]),
    ("Mo-Fr 09:00-17:00; Sa 10:00-13:00; Su off", 6, []),
    ("09:00-12:00,14:00-17:00", 0, [(540, 720), (840, 1020)]),
    ("Mo-Su 09:00-17:00; PH off", 0, None), ("18:00-02:00", 0, None),
    ("", 0, None), ("09:99-17:00", 0, None),
])
def test_weekly_hours_are_conservative(text, day, expected):
    assert opening_windows(text, day) == expected


def make_place(identifier, hours="24/7", category="museum"):
    return {"id": identifier, "name": identifier, "lat": 35.0, "lng": 135.0,
            "category": category, "opening_hours": hours}


def test_closed_dates_duration_and_no_duplicate_visits():
    start, end = date(2026, 9, 14), date(2026, 9, 15)
    destination = {"name": "Test", "lat": 35.0, "lng": 135.0}
    places = [make_place("closed_monday", "Tu 10:00-17:00"),
              make_place("short", "09:00-09:30"), make_place("unknown", "by appointment")]
    result = build_plan(places, destination, start, end)
    assert [day["date"] for day in result["days"]] == ["2026-09-14", "2026-09-15"]
    assert result["days"][0]["stops"][0]["place"]["id"] == "unknown"
    assert result["days"][0]["stops"][0]["hours_status"] == "unverified"
    tuesday = result["days"][1]["stops"]
    assert tuesday[0]["place"]["id"] == "closed_monday"
    assert tuesday[0]["arrival"] == "10:00"
    ids = [stop["place"]["id"] for day in result["days"] for stop in day["stops"]]
    assert len(ids) == len(set(ids)) == 2


def test_empty_results_are_empty_days_not_invented_places():
    result = build_plan([], {"name": "Desert", "lat": 0, "lng": 0}, date(2026, 9, 14), date(2026, 9, 14))
    assert result["scheduled_count"] == 0
    assert result["days"][0]["stops"] == []
