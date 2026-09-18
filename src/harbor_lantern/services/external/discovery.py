"""Destination and place discovery; public OSM data and optional Google reviews."""

import math
import os
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.services.external.ports import ExternalUnavailable

USER_AGENT = "HarborLantern/0.2 (personal travel planner)"


def safe_link(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    return value if parsed.scheme in ("https", "http") and parsed.netloc and not parsed.username else None


class DiscoveryProvider:
    def __init__(self, client=None):
        self.client = client
        self._cache = {}
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._cooldown = {}

    def _cached(self, key, loader):
        # Serialize public-service calls, cap cache size, never cache Google reviews.
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < 1800:
                return deepcopy(cached[1])
            wait = 1.1 - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            value = loader()
            if len(self._cache) >= 100:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic(), value)
            return deepcopy(value)

    def _request(self, method, url, **kwargs):
        try:
            if self.client:
                response = self.client.request(method, url, **kwargs)
            else:
                with httpx.Client(timeout=35, headers={"User-Agent": USER_AGENT}) as client:
                    response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalUnavailable("장소 제공자에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc

    def _overpass(self, query):
        configured = os.environ.get("HL_DISCOVERY_OVERPASS_URL")
        endpoints = [configured] if configured else [
            "https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
        for endpoint in endpoints:
            if self._cooldown.get(endpoint, 0) > time.monotonic():
                continue
            try:
                document = self._request("POST", endpoint, data={"data": query}, timeout=10)
                if document.get("remark"):
                    raise ExternalUnavailable("장소 조회가 시간 내 완료되지 않았습니다.")
                return document
            except ExternalUnavailable:
                self._cooldown[endpoint] = time.monotonic() + 30
        raise ExternalUnavailable("장소 제공자가 혼잡합니다. 30초 후 다시 시도하거나 검색 반경을 줄여 주세요.")

    def _limited_search(self, lat, lng, radius, restaurants_only):
        """User-triggered best matches, never paginate or bulk-download from Nominatim."""
        dy = radius / 111000
        dx = min(180, dy / max(0.01, math.cos(math.radians(lat))))
        box = f"{max(-180,lng-dx)},{min(90,lat+dy)},{min(180,lng+dx)},{max(-90,lat-dy)}"
        categories = [("restaurant", "amenity")] if restaurants_only else [
            ("restaurant", "amenity"), ("museum", "tourism"), ("park", "leisure")]
        elements = []
        for category, tag in categories:
            wait = 1.1 - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            rows = self._request("GET", "https://nominatim.openstreetmap.org/search", timeout=8,
                                 headers={"User-Agent": USER_AGENT}, params={
                                     "q": f"[{category}]", "viewbox": box, "bounded": 1,
                                     "format": "jsonv2", "extratags": 1, "limit": 30})
            for row in rows:
                if row.get("type") != category or not row.get("name"):
                    continue
                elements.append({"type": row["osm_type"], "id": row["osm_id"],
                                 "lat": float(row["lat"]), "lon": float(row["lon"]),
                                 "tags": {**(row.get("extratags") or {}), "name": row["name"], tag: category}})
        return {"elements": elements, "limited_search": True}

    def destinations(self, query):
        def fetch():
            rows = self._request("GET", "https://nominatim.openstreetmap.org/search", params={
                "q": query, "format": "jsonv2", "limit": 6, "addressdetails": 1,
                "accept-language": "ko,en"}, headers={"User-Agent": USER_AGENT})
            return [{"id": str(row["place_id"]), "name": row["display_name"],
                     "lat": float(row["lat"]), "lng": float(row["lon"]),
                     "kind": row.get("addresstype", "place"),
                     "country": row.get("address", {}).get("country", "")}
                    for row in rows if "lat" in row and "lon" in row]
        return self._cached(("destination", query.casefold()), fetch)

    def search_places(self, query, lat, lng):
        """Explicit name search, capped and cached; no autocomplete or bulk lookup."""
        def fetch():
            dy = 50000 / 111000
            dx = min(180, dy / max(0.01, math.cos(math.radians(lat))))
            box = f"{max(-180,lng-dx)},{min(90,lat+dy)},{min(180,lng+dx)},{max(-90,lat-dy)}"
            rows = self._request("GET", "https://nominatim.openstreetmap.org/search", params={
                "q": query, "format": "jsonv2", "limit": 10, "extratags": 1, "namedetails": 1,
                "viewbox": box, "bounded": 1, "accept-language": "ko,en"},
                headers={"User-Agent": USER_AGENT})
            result = []
            for row in rows:
                try:
                    point = LatLng(float(row["lat"]), float(row["lon"]))
                    osm_id = str(int(row["osm_id"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if (row.get("osm_type") not in {"node", "way", "relation"}
                        or not math.isfinite(point.lat) or not math.isfinite(point.lng)
                        or not -90 <= point.lat <= 90 or not -180 <= point.lng <= 180):
                    continue
                distance = round(haversine_m(LatLng(lat, lng), point))
                if distance > 50000:
                    continue
                tags = row.get("extratags") or {}
                names = row.get("namedetails") or {}
                source = f'https://www.openstreetmap.org/{row["osm_type"]}/{osm_id}'
                result.append({
                    "id": f'{row["osm_type"]}/{osm_id}', "wikidata_id": tags.get("wikidata"),
                    "name": (names.get("name:ko") or row.get("name") or row["display_name"].split(",")[0])[:400],
                    "name_original": names.get("name", ""), "address": row.get("display_name", ""),
                    "lat": point.lat, "lng": point.lng, "distance_m": distance,
                    "category": row.get("type", "place"), "hours_text": tags.get("opening_hours", "")[:2000],
                    "source": "OpenStreetMap", "source_url": source,
                    "fetched_at": datetime.now(UTC).isoformat(), "website": safe_link(tags.get("website")),
                })
            return result
        return self._cached(("place-search", query.casefold(), lat, lng), fetch)

    def places(self, lat, lng, radius, restaurants_only=False):
        def fetch():
            near = f"(around:{radius},{float(lat)},{float(lng)})"
            clauses = [f'nwr{near}["amenity"~"^(restaurant|cafe|fast_food)$"]["name"];']
            if not restaurants_only:
                clauses += [f'nwr{near}["tourism"~"^(attraction|museum|gallery|viewpoint|zoo)$"]["name"];',
                            f'nwr{near}["leisure"="park"]["name"];']
            # Separate per-category caps avoid restaurants crowding out every attraction.
            limit = 100 if not restaurants_only else 150
            query = "[out:json][timeout:25];" + "".join(clause + f"out center {limit};" for clause in clauses)
            try:
                document = self._overpass(query)
            except ExternalUnavailable:
                document = self._limited_search(lat, lng, radius, restaurants_only)
            if document.get("remark"):
                raise ExternalUnavailable("장소 조회가 시간 내 완료되지 않았습니다. 반경을 줄여 다시 시도하세요.")
            places = []
            for row in document.get("elements", []):
                tags = row.get("tags", {})
                coord = row.get("center", row)
                if "lat" not in coord or "lon" not in coord or not tags.get("name"):
                    continue
                kind = tags.get("amenity") or tags.get("tourism") or tags.get("leisure", "place")
                cuisine = tags.get("cuisine", "").replace(";", " · ")
                places.append({
                    "id": f'{row["type"]}/{row["id"]}', "name": tags.get("name:ko", tags["name"]),
                    "lat": coord["lat"], "lng": coord["lon"], "category": kind,
                    "opening_hours": tags.get("opening_hours", ""), "cuisine": cuisine,
                    "menu_url": safe_link(tags.get("website:menu") or tags.get("contact:menu")),
                    "website": safe_link(tags.get("website") or tags.get("contact:website")),
                    "recommendation": None, "rating": None, "review_count": None, "reviews": [],
                    "source": "OpenStreetMap", "source_url": f'https://www.openstreetmap.org/{row["type"]}/{row["id"]}',
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "limited_search": document.get("limited_search", False),
                })
            unique = []
            for place in places:
                if any(place["name"].casefold() == other["name"].casefold() and haversine_m(
                    LatLng(place["lat"], place["lng"]), LatLng(other["lat"], other["lng"])) < 60 for other in unique):
                    continue
                unique.append(place)
            return unique
        return self._cached(("places", round(lat, 4), round(lng, 4), radius, restaurants_only), fetch)

    @property
    def reviews_enabled(self):
        return bool(os.environ.get("HL_GOOGLE_PLACES_API_KEY", "").strip())

    def reviewed_restaurants(self, lat, lng, radius):
        key = os.environ.get("HL_GOOGLE_PLACES_API_KEY", "").strip()
        if not key:
            return None
        fields = ("places.id,places.displayName,places.location,places.rating,places.userRatingCount,"
                  "places.reviews,places.googleMapsUri,places.regularOpeningHours,places.websiteUri")
        document = self._request("POST", "https://places.googleapis.com/v1/places:searchNearby", headers={
            "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields}, json={
            "includedTypes": ["restaurant"], "maxResultCount": 15, "languageCode": "ko",
            "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius}}})
        return [{
            "id": row["id"], "name": row.get("displayName", {}).get("text", "이름 미제공"),
            "lat": row["location"]["latitude"], "lng": row["location"]["longitude"], "category": "restaurant",
            "rating": row.get("rating"), "review_count": row.get("userRatingCount"),
            "opening_hours": " / ".join(row.get("regularOpeningHours", {}).get("weekdayDescriptions", [])),
            "cuisine": "", "recommendation": None, "menu_url": None, "website": safe_link(row.get("websiteUri")),
            "source": "Google Maps", "source_url": safe_link(row.get("googleMapsUri")),
            "reviews": [{"text": item.get("text", {}).get("text", ""), "rating": item.get("rating"),
                         "author": item.get("authorAttribution", {}).get("displayName", "Google Maps 사용자"),
                         "author_url": safe_link(item.get("authorAttribution", {}).get("uri")),
                         "avatar": safe_link(item.get("authorAttribution", {}).get("photoUri")),
                         "url": safe_link(item.get("googleMapsUri")),
                         "date": item.get("relativePublishTimeDescription", "")}
                        for item in row.get("reviews", [])[:3]],
            "fetched_at": datetime.now(UTC).isoformat(),
        } for row in document.get("places", []) if "location" in row]
