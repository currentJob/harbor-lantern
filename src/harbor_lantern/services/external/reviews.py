"""Explicit, bounded Google Places review lookup. No review persistence or cache."""

import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.services.external.discovery import safe_link
from harbor_lantern.services.external.ports import ExternalUnavailable

MAX_LOOKUPS = 40
_slots = threading.BoundedSemaphore(1)


def _normal(name):
    return "".join(c for c in unicodedata.normalize("NFKC", name).casefold() if c.isalnum())


def lookup(provider, spot):
    import os

    name = spot.get("name_original") or spot["name"]
    # Explanatory parentheticals in the seed are not part of the place name.
    name = re.split(r"[（(]", name)[0].strip()
    fields = ("places.id,places.displayName,places.location,places.rating,places.userRatingCount,"
              "places.reviews,places.googleMapsUri,places.businessStatus")
    try:
        document = provider._request("POST", "https://places.googleapis.com/v1/places:searchText", timeout=5,
                                     headers={"X-Goog-Api-Key": os.environ.get("HL_GOOGLE_PLACES_API_KEY", ""),
                                              "X-Goog-FieldMask": fields}, json={
                                         "textQuery": name, "pageSize": 3,
                                         "locationBias": {"circle": {"center": {
                                             "latitude": spot["lat"], "longitude": spot["lng"]}, "radius": 500}}})
    except ExternalUnavailable:
        return {"status": "unavailable"}
    aliases = {_normal(name), _normal(spot["name"])} - {""}
    candidates = []
    for row in document.get("places", []):
        coord = row.get("location", {})
        if "latitude" not in coord or "longitude" not in coord:
            continue
        distance = haversine_m(LatLng(spot["lat"], spot["lng"]), LatLng(coord["latitude"], coord["longitude"]))
        if distance <= 300 and _normal(row.get("displayName", {}).get("text", "")) in aliases:
            candidates.append(row)
    if len(candidates) != 1:
        return {"status": "unmatched"}  # Ambiguous/wrong branch: never attach somebody else's rating.
    row = candidates[0]
    return {"status": "matched", "place_id": row["id"], "name": row.get("displayName", {}).get("text", name),
            "rating": row.get("rating"), "review_count": row.get("userRatingCount"),
            "business_status": row.get("businessStatus"), "source": "Google Maps",
            "source_url": safe_link(row.get("googleMapsUri")), "fetched_at": datetime.now(UTC).isoformat(),
            "reviews": [{"text": item.get("text", {}).get("text", ""), "rating": item.get("rating"),
                         "author": item.get("authorAttribution", {}).get("displayName", "Google Maps 사용자"),
                         "author_url": safe_link(item.get("authorAttribution", {}).get("uri")),
                         "url": safe_link(item.get("googleMapsUri")), "date": item.get("publishTime", "")}
                        for item in row.get("reviews", [])[:3]]}


def collect_reviews(provider, spots, enabled):
    status = "not_requested" if not enabled else "disabled"
    evidence = {s["id"]: {"status": status} for s in spots}
    if not enabled or not provider.reviews_enabled:
        return evidence
    if not _slots.acquire(blocking=False):
        return {s["id"]: {"status": "busy"} for s in spots}
    try:
        chosen = spots[:MAX_LOOKUPS]
        evidence = {s["id"]: {"status": "limit"} for s in spots}
        def fetch(spot):
            try:
                return lookup(provider, spot)
            except (ValueError, TypeError, KeyError, AttributeError):
                return {"status": "unavailable"}

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(fetch, chosen))
        evidence.update({s["id"]: result for s, result in zip(chosen, results, strict=True)})
        return evidence
    finally:
        _slots.release()


def review_summary(evidence):
    counts = {}
    for item in evidence.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"counts": counts, "notice": (
        "평점과 평가 수를 함께 반영합니다. 이름·좌표가 일치한 장소만 사용하며 미확인 평점은 추정하지 않습니다. "
        "Google Maps 후기는 관련성순 일부이며 전체 후기나 최신순을 뜻하지 않습니다.")}
