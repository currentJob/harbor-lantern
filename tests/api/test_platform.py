"""Offline rating evidence, conservative matching and explicit-order edits."""

from harbor_lantern.services.ratings import city_ratings, rating_catalog


def test_all_catalogue_cities_have_traceable_ratings(client):
    cities = client.get('/api/explore/guides').json()['cities']
    for city in cities:
        rows = city_ratings(city['city_id'])
        assert rows and city['rated_count'] == len(rows)
        for row in rows.values():
            assert row['source'] == 'Trip.com'
            assert row['source_url'].startswith('https://www.trip.com/travel-guide/attraction/')
            assert row['match_method'] == 'city-and-exact-wikidata-label'
            assert 'reviews' not in row


def test_snapshot_rating_survives_missing_google_key(client, app):
    from tests.api.test_explore import FakeDiscovery
    app.state.discovery = FakeDiscovery()
    response = client.post('/api/explore/plan', json={
        'city_id': 'prague', 'start_date': '2026-10-05', 'end_date': '2026-10-07', 'use_reviews': True})
    assert response.status_code == 200
    plan = response.json()
    ratings = [s['place'].get('review') for d in plan['days'] for s in d['stops']]
    assert any(r and r.get('source') == 'Trip.com' for r in ratings)
    assert plan['rating_summary']['matched'] > 0
    assert 'reviews' not in next(iter(rating_catalog()['prague'].values()))


def test_recalculate_keeps_order_checks_hours_and_recomputes_legs(client):
    places = [
        {'id': 'a', 'name': 'A', 'lat': 50.08, 'lng': 14.4, 'hours_text': 'Mo-Su 10:00-18:00'},
        {'id': 'b', 'name': 'B', 'lat': 50.09, 'lng': 14.41, 'hours_text': 'Mo off'},
    ]
    response = client.post('/api/explore/recalculate', json={'days': [
        {'date': '2026-10-05', 'stops': [{'place': p, 'duration': 60, 'completed': True} for p in places]},
        {'date': '2026-10-06', 'stops': []},
    ]})
    assert response.status_code == 200, response.text
    body = response.json()
    stops = body['days'][0]['stops']
    assert [s['place']['id'] for s in stops] == ['a', 'b']
    assert stops[0]['arrival'] == '10:00' and stops[0]['completed']
    assert stops[1]['warnings'] and stops[1]['distance_m'] > 0
    assert body['days'][0]['distance_m'] == stops[1]['distance_m']
    assert body['days'][1]['stops'] == [] and body['scheduled_count'] == 2
    places[0]['lat'] = 999
    assert client.post('/api/explore/recalculate', json={'days': [
        {'date': '2026-10-05', 'stops': [{'place': places[0]}]}]}).status_code == 422
