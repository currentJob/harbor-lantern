"""Review lookup is optional; proposals preserve all stops and cannot overwrite newer edits."""

import json

import httpx

from harbor_lantern.services.external.discovery import DiscoveryProvider
from harbor_lantern.services.external.reviews import collect_reviews


def test_all_days_read_only_and_routes(trip, monkeypatch):
    monkeypatch.delenv("HL_GOOGLE_PLACES_API_KEY", raising=False)
    before = trip.state().json()
    response = trip.client.post(f"{trip.base}/review-plan", headers=trip.headers, json={"use_reviews": True})
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert result["expected_revision"] == before["trip"]["revision"]
    assert result["review_summary"]["counts"] == {"disabled": 27}
    assert len(result["days"]) == 4
    for report, original in zip(result["days"], before["days"], strict=True):
        ids = [s["id"] for s in original["spots"]]
        assert set(report["proposed_order"]) == set(ids)
        assert report["proposed_order"][0] == ids[0]
        assert report["proposed_order"][-1] == ids[-1]
        assert len(report["routes"]) == len(ids) - 1
        assert len(report["proposed"]["warnings"]) <= len(report["current"]["warnings"])
        for route in report["routes"]:
            assert "origin=" in route["directions"]["transit"]
            assert "travelmode=walking" in route["directions"]["walking"]
    assert trip.state().json() == before


def test_auth_validation_and_stale_apply(trip):
    url = f"{trip.base}/review-plan"
    assert trip.client.post(url, json={}).status_code == 404
    assert trip.client.post(url, headers=trip.headers, json={"invented": True}).status_code == 422
    result = trip.client.post(url, headers=trip.headers, json={}).json()
    report = result["days"][0]
    done = trip.client.put(f"{trip.base}/spots/{report['proposed_order'][0]}/done",
                           headers=trip.headers, json={"done": True})
    assert done.status_code == 200
    apply = trip.client.put(f"{trip.base}/days/1/order", headers=trip.headers,
                           json={"expected_revision": result["expected_revision"],
                                 "spot_ids": report["proposed_order"]})
    assert apply.status_code == 409


def test_live_review_attribution_failure_and_identity(monkeypatch):
    monkeypatch.setenv("HL_GOOGLE_PLACES_API_KEY", "test-only-not-a-real-credential")
    calls = []

    def handler(request):
        query = json.loads(request.content)["textQuery"]
        calls.append(query)
        if query == "Failure":
            return httpx.Response(503)
        row = {"id": query, "displayName": {"text": "Wrong branch" if query == "Mismatch" else query},
               "location": {"latitude": 22.3, "longitude": 114.17},
               "rating": 4.7, "userRatingCount": 100,
               "googleMapsUri": "https://maps.google.com/example",
               "reviews": [{"text": {"text": "Great"}, "rating": 5, "publishTime": "2026-09-01T00:00:00Z",
                            "authorAttribution": {"displayName": "Author", "uri": "javascript:alert(1)"}}]}
        if query == "Far":
            row["location"]["latitude"] = 23.3
        return httpx.Response(200, json={"places": [row, row] if query == "Ambiguous" else [row]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DiscoveryProvider(client)
        spots = [{"id": name, "name": name, "lat": 22.3, "lng": 114.17}
                 for name in ["Matched", "Mismatch", "Far", "Ambiguous", "Failure"]]
        assert collect_reviews(provider, spots, False)["Matched"]["status"] == "not_requested"
        assert not calls
        result = collect_reviews(provider, spots, True)
        assert result["Matched"]["status"] == "matched"
        assert result["Matched"]["reviews"][0]["author"] == "Author"
        assert result["Matched"]["reviews"][0]["author_url"] is None
        assert result["Failure"]["status"] == "unavailable"
        assert all(result[name]["status"] == "unmatched" for name in ["Mismatch", "Far", "Ambiguous"])
        assert not provider._cache


def test_lookup_bound_and_disabled(monkeypatch):
    from harbor_lantern.services.external import reviews

    provider = DiscoveryProvider()
    spots = [{"id": str(i), "name": "Spot", "lat": 22.3, "lng": 114.17} for i in range(45)]
    monkeypatch.delenv("HL_GOOGLE_PLACES_API_KEY", raising=False)
    assert all(r["status"] == "disabled" for r in collect_reviews(provider, spots, True).values())
    monkeypatch.setenv("HL_GOOGLE_PLACES_API_KEY", "test-only-not-a-real-credential")
    calls = []
    monkeypatch.setattr(reviews, "lookup", lambda p, s: calls.append(s["id"]) or {"status": "unmatched"})
    result = collect_reviews(provider, spots, True)
    assert len(calls) == 40
    assert sum(r["status"] == "limit" for r in result.values()) == 5
    assert reviews._slots.acquire(blocking=False)
    try:
        assert all(r["status"] == "busy" for r in collect_reviews(provider, spots, True).values())
    finally:
        reviews._slots.release()


def test_empty_day_and_completed_prefix(trip):
    day = trip.day(1)
    for spot in day["spots"]:
        trip.client.delete(f"{trip.base}/spots/{spot['id']}", headers=trip.headers)
    second = trip.day(2)
    trip.client.put(f"{trip.base}/spots/{second['spots'][3]['id']}/done", headers=trip.headers, json={"done": True})
    report = trip.client.post(f"{trip.base}/review-plan", headers=trip.headers, json={}).json()
    assert report["days"][0]["proposed_order"] == []
    assert report["days"][0]["routes"] == []
    assert report["days"][1]["proposed_order"][:4] == [s["id"] for s in second["spots"][:4]]


def test_reviews_change_real_proposal_and_apply(trip, monkeypatch):
    from harbor_lantern.services.external import reviews

    monkeypatch.setenv("HL_GOOGLE_PLACES_API_KEY", "test-only-not-a-real-credential")
    monkeypatch.setattr(reviews, "lookup", lambda provider, spot: {
        "status": "matched", "rating": 5 if spot["name"] == "High" else 2, "review_count": 1000})
    for spot in trip.day(1)["spots"]:
        trip.client.delete(f"{trip.base}/spots/{spot['id']}", headers=trip.headers)
    ids = []
    for name in ["Start", "Low", "High", "End"]:
        created = trip.client.post(f"{trip.base}/days/1/spots", headers=trip.headers, json={
            "name": name, "lat": 22.3, "lng": 114.17, "time_label": "오후", "dwell_minutes": 60})
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])
    offline = trip.client.post(f"{trip.base}/review-plan", headers=trip.headers, json={}).json()
    assert offline["days"][0]["proposed_order"] == ids
    live = trip.client.post(f"{trip.base}/review-plan", headers=trip.headers, json={"use_reviews": True}).json()
    assert live["days"][0]["proposed_order"] == [ids[0], ids[2], ids[1], ids[3]]
    applied = trip.client.put(f"{trip.base}/days/1/order", headers=trip.headers, json={
        "expected_revision": live["expected_revision"], "spot_ids": live["days"][0]["proposed_order"]})
    assert applied.status_code == 200
    assert trip.spot_ids(1) == [ids[0], ids[2], ids[1], ids[3]]


def test_explore_review_option_without_key(app, client, monkeypatch):
    from tests.api.test_explore import FakeDiscovery

    monkeypatch.delenv("HL_GOOGLE_PLACES_API_KEY", raising=False)
    app.state.discovery = FakeDiscovery()
    for target in [{"city_id": "hong-kong"}, {"destination": {"name": "HK", "lat": 22.3, "lng": 114.17}}]:
        response = client.post("/api/explore/plan", json={**target, "start_date": "2026-10-05",
                                                       "end_date": "2026-10-08", "use_reviews": True,
                                                       "use_ratings": False})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["review_summary"]["counts"]["disabled"] > 0
        assert all(stop["place"]["review"]["status"] == "disabled" for day in body["days"] for stop in day["stops"])
