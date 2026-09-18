"""Review-aware, bounded itinerary proposals. Pure calculations; never writes a trip."""

import math

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.util import parse_hhmm


def review_score(evidence):
    """Shrink small samples toward neutral 3.5; missing evidence has no ranking effect."""
    if not evidence or evidence.get("status") != "matched":
        return None
    rating, count = evidence.get("rating"), evidence.get("review_count")
    if (isinstance(rating, bool) or not isinstance(rating, (int, float))
            or not math.isfinite(rating) or not 1 <= rating <= 5
            or isinstance(count, bool) or not isinstance(count, int) or count <= 0):
        return None
    return (rating * count + 3.5 * 50) / (count + 50)


def review_bonus(evidence):
    score = review_score(evidence)
    return 0.0 if score is None else max(-15.0, min(15.0, (score - 3.5) * 10))


def reported_closed(spot):
    review = spot.get("review") or {}
    return review.get("status") == "matched" and review.get("business_status") in {
        "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"}


def propose_order(spots, evaluate):
    """Swap within contiguous time bands; retain endpoints, reservations and completed prefix.

    evaluate(order) returns (problem identifiers, weighted cost, payload). New problems
    are never introduced, and each accepted swap must strictly improve the objective.
    A maximum of four passes / 40 stops bounds user-edited itineraries.
    """
    order = list(spots)
    current = evaluate(order)
    best = current
    completed = max((i for i, s in enumerate(order) if s.get("done", {}).get("is_done")), default=-1)
    groups = []
    group = 0
    for i, spot in enumerate(order):
        if i and spot.get("time_label") != order[i - 1].get("time_label"):
            group += 1
        groups.append(group)
    free = [i for i, s in enumerate(order) if completed < i < len(order) - 1 and i > 0
            and not s.get("fixed_start_local") and parse_hhmm(s.get("time_label", "")) is None]
    # Even an unlabelled intermediate reservation divides the movable segment.
    for i in range(1, len(order)):
        if i - 1 not in free:
            for j in range(i, len(groups)):
                groups[j] += 1
    if len(order) <= 40:
        for _ in range(4):
            improved = False
            for a, i in enumerate(free):
                for j in free[a + 1:]:
                    if groups[i] != groups[j]:
                        continue
                    candidate = order.copy()
                    candidate[i], candidate[j] = candidate[j], candidate[i]
                    result = evaluate(candidate)
                    if result[0] <= best[0] and (len(result[0]), result[1]) < (len(best[0]), best[1] - 1e-7):
                        order, best, improved = candidate, result, True
            if not improved:
                break
    return order, current, best


def route_links(origin, destination):
    """Real navigation is delegated to Google Maps; no fabricated turn instructions."""
    base = ("https://www.google.com/maps/dir/?api=1"
            f"&origin={origin['lat']},{origin['lng']}"
            f"&destination={destination['lat']},{destination['lng']}")
    return {"walking": base + "&travelmode=walking", "transit": base + "&travelmode=transit"}


def legs_for(spots):
    return [{"from_spot_id": a["id"], "to_spot_id": b["id"],
             "from_name": a["name"], "to_name": b["name"],
             "distance_m": round(haversine_m(LatLng(a["lat"], a["lng"]), LatLng(b["lat"], b["lng"]))),
             "directions": route_links(a, b)} for a, b in zip(spots, spots[1:], strict=False)]
