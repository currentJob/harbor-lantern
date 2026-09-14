"""Provider isolation and public planning API contracts."""

import httpx

from harbor_lantern.services.external.discovery import DiscoveryProvider, safe_link
from harbor_lantern.services.external.ports import ExternalUnavailable


class FakeDiscovery:
    reviews_enabled = False

    def destinations(self, query):
        return [{"name": query, "lat": 35, "lng": 135, "id": "test", "kind": "city"}]

    def places(self, lat, lng, radius, restaurants_only=False):
        return [{"id": "one", "name": "Real sample", "lat": lat, "lng": lng,
                 "category": "restaurant" if restaurants_only else "museum", "opening_hours": "24/7",
                 "rating": None, "reviews": []}]

    def reviewed_restaurants(self, lat, lng, radius):
        return None


def test_explore_contract(app, client):
    app.state.discovery = FakeDiscovery()
    assert client.get("/api/explore/destinations?q=Osaka").json()["items"][0]["name"] == "Osaka"
    body = {"destination": {"name": "Osaka", "lat": 35, "lng": 135},
            "start_date": "2026-09-14", "end_date": "2026-09-16"}
    response = client.post("/api/explore/plan", json=body)
    assert response.status_code == 200, response.text
    assert len(response.json()["days"]) == 3
    assert response.json()["scheduled_count"] == 1
    assert client.post("/api/explore/plan", json={**body, "end_date": "2026-10-10"}).status_code == 422
    assert client.post("/api/explore/plan", json={**body, "end_date": "2026-09-13"}).status_code == 422
    nearby = client.get("/api/explore/nearby?lat=35&lng=135").json()
    assert nearby["places"][0]["distance_m"] == 0
    assert nearby["places"][0]["rating"] is None
    assert client.get("/api/explore/nearby?lat=nan&lng=135").status_code == 422
    assert client.get("/api/explore/nearby?lat=35&lng=135&radius_m=9000").status_code == 422


def test_provider_failure_is_retryable(app, client):
    class Unavailable(FakeDiscovery):
        def destinations(self, query):
            raise ExternalUnavailable("temporarily unavailable")
    app.state.discovery = Unavailable()
    assert client.get("/api/explore/destinations?q=Osaka").status_code == 503
    app.state.discovery = FakeDiscovery()
    assert client.get("/api/explore/destinations?q=Osaka").status_code == 200


def test_osm_metadata_is_preserved_without_fabricated_reviews():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"elements": [{"type": "node", "id": 10, "lat": 35, "lon": 135,
            "tags": {"name": "Sample", "amenity": "restaurant", "cuisine": "japanese",
                     "opening_hours": "Mo-Fr 09:00-17:00", "website:menu": "https://example.org/menu"}}]})
    provider = DiscoveryProvider(httpx.Client(transport=httpx.MockTransport(handler)))
    places = provider.places(35, 135, 800, True)
    assert places[0]["opening_hours"] == "Mo-Fr 09:00-17:00"
    assert places[0]["menu_url"] == "https://example.org/menu"
    assert places[0]["rating"] is None and places[0]["reviews"] == []
    assert provider.places(35, 135, 800, True) == places
    assert len(calls) == 1
    assert safe_link("javascript:alert(1)") is None


def test_optional_google_rating_and_review_attribution(monkeypatch):
    monkeypatch.setenv("HL_GOOGLE_PLACES_API_KEY", "test-only-not-a-real-credential")
    def handler(request):
        assert request.headers["X-Goog-Api-Key"] == "test-only-not-a-real-credential"
        return httpx.Response(200, json={"places": [{"id": "g1", "displayName": {"text": "Sample"},
            "location": {"latitude": 35, "longitude": 135}, "rating": 4.5, "userRatingCount": 12,
            "reviews": [{"text": {"text": "Good food"}, "rating": 5,
                         "authorAttribution": {"displayName": "Reviewer", "uri": "https://example.org/author"},
                         "googleMapsUri": "https://example.org/review"}]}]})
    provider = DiscoveryProvider(httpx.Client(transport=httpx.MockTransport(handler)))
    place = provider.reviewed_restaurants(35, 135, 800)[0]
    assert place["rating"] == 4.5
    assert place["reviews"][0]["author"] == "Reviewer"
    assert place["reviews"][0]["url"] == "https://example.org/review"
    assert provider._cache == {}
