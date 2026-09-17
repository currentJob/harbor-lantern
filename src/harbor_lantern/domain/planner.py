"""Deterministic distance/time-window heuristic. No claims of a globally optimal route."""

import re
from datetime import timedelta

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.review_plan import reported_closed, review_bonus

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
FOOD = {"restaurant", "cafe", "fast_food"}


def travel_minutes(distance_m):
    """Straight-line distance -> minutes. Walking below 1.2km, transit above with access overhead.

    Extracted verbatim from `build_plan` so the guide planner (`domain/guide.py`, DSN-43)
    computes the same minutes. Two paths quoting different travel times show up on one screen.
    """
    if distance_m <= 1200:
        return max(1, round(distance_m * 1.35 / 80))
    return round(distance_m * 1.45 / 366) + 8


def opening_windows(text, weekday):
    """Conservative OSM weekly subset; unsupported holidays/seasonal syntax stays unknown."""
    if text == "24/7":
        return [(0, 1440)]
    if not text:
        return None
    windows = []
    for rule in text.split(";"):
        match = re.fullmatch(r"\s*(?:(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?\s+)?"
                             r"(off|closed|(?:\d{2}:\d{2}-\d{2}:\d{2})(?:,\d{2}:\d{2}-\d{2}:\d{2})*)\s*", rule)
        if not match:
            return None
        start, end, hours = match.groups()
        if start:
            weekdays = {DAYS.index(start)}
            if end:
                cursor = DAYS.index(start)
                while cursor != DAYS.index(end):
                    cursor = (cursor + 1) % 7
                    weekdays.add(cursor)
            if weekday not in weekdays:
                continue
        if hours in ("off", "closed"):
            windows = []
            continue
        for span in hours.split(","):
            values = [int(v) for v in re.split("[:-]", span)]
            if values[0] > 23 or values[2] > 24 or values[1] > 59 or values[3] > 59:
                return None
            opened, closed = values[0] * 60 + values[1], values[2] * 60 + values[3]
            if closed <= opened:
                return None  # Overnight carry needs previous-day rules; do not guess.
            windows.append((opened, closed))
    return windows


def build_plan(places, destination, start, end, pace="balanced", interests="mixed"):
    used = set()
    days = []
    count = {"relaxed": 3, "balanced": 5, "full": 7}[pace]
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        cursor = 9 * 60
        position = LatLng(destination["lat"], destination["lng"])
        stops = []
        for _ in range(count):
            ranked = []
            for place in places:
                if place["id"] in used or reported_closed(place):
                    continue
                food = place["category"] in FOOD
                if food and sum(stop["place"]["category"] in FOOD for stop in stops) >= 2:
                    continue
                if interests == "culture" and place["category"] in {"park", "viewpoint"}:
                    continue
                if interests == "nature" and place["category"] in {"museum", "gallery"}:
                    continue
                distance = haversine_m(position, LatLng(place["lat"], place["lng"]))
                # Approximate walking / transit including access overhead.
                travel = travel_minutes(distance)
                duration = 60 if food else 90
                eta = cursor + (travel if stops else 0)
                windows = opening_windows(place.get("opening_hours", ""), day.weekday())
                if windows is not None:
                    feasible = [max(eta, a) for a, b in windows if max(eta, a) + duration <= b]
                    if not feasible:
                        continue
                    eta = min(feasible)
                if eta + duration > 19 * 60 or eta - cursor > 150:
                    continue
                meal_time = 11 * 60 <= eta <= 14 * 60 or eta >= 17 * 60
                meal_penalty = 0 if meal_time == food else 100
                if food and stops and stops[-1]["place"]["category"] in FOOD:
                    meal_penalty += 150
                rank = travel + (eta - cursor) + meal_penalty + (15 if windows is None else 0)
                rank -= review_bonus(place.get("review"))
                ranked.append((rank, place["id"], place, eta, duration, travel, distance, windows))
            if not ranked:
                break
            _, _, place, eta, duration, travel, distance, windows = min(ranked, key=lambda item: item[:2])
            stops.append({"place": place, "arrival": f"{eta // 60:02}:{eta % 60:02}",
                          "departure": f"{(eta + duration) // 60:02}:{(eta + duration) % 60:02}",
                          "travel_minutes": travel if len(stops) else 0,
                          "distance_m": round(distance) if len(stops) else 0,
                          "hours_status": "weekly_hours" if windows is not None else "unverified"})
            used.add(place["id"])
            position = LatLng(place["lat"], place["lng"])
            cursor = eta + duration
        days.append({"date": day.isoformat(), "weekday": day.weekday(), "stops": stops,
                     "distance_m": sum(stop["distance_m"] for stop in stops),
                     "travel_minutes": sum(stop["travel_minutes"] for stop in stops)})
    return {"destination": destination, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "days": days, "algorithm": "distance-and-opening-window-heuristic",
            "notice": ("현지 날짜·요일과 공개 영업시간을 반영한 추천 동선입니다. "
                       "이동은 추정치이며 공휴일·임시휴무는 방문 전 확인하세요."),
            "candidate_count": len(places), "scheduled_count": len(used)}
