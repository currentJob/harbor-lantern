import httpx

from harbor_lantern.services.external.discovery import DiscoveryProvider


def stop(name, lng, **extra):
    return {"place": {"id": name, "name": name, "lat": 50, "lng": lng}, "duration": 30, **extra}


def test_best_insertion_preserves_order_fixed_times_and_manual_override(client):
    a, b, c = stop("a", 14), stop("b", 14.02, fixed_start="12:00"), stop("c", 14.01)
    day = {"date": "2026-10-05", "stops": [a, b]}
    result = client.post('/api/explore/recalculate', json={"days": [day], "insert": {"day_index": 0, "stop": c}})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['insertion']['stop_index'] == 1
    stops = body['days'][0]['stops']
    assert [s['place']['id'] for s in stops] == ['a', 'c', 'b']
    assert stops[-1]['fixed_start'] == stops[-1]['arrival'] == '12:00'
    # Explicit reordering is respected; the requested time remains and a conflict is visible.
    day['stops'] = [b, c, {**a, 'fixed_start': '09:00'}]
    manual = client.post('/api/explore/recalculate', json={'days': [day]}).json()['days'][0]
    assert [s['place']['id'] for s in manual['stops']] == ['b', 'c', 'a']
    assert manual['stops'][-1]['fixed_start'] == '09:00'
    assert any('늦게' in w for w in manual['stops'][-1]['warnings'])


def test_insertion_empty_day_completed_prefix_duplicate_and_capacity(client):
    day = {'date': '2026-10-05', 'start_time': '08:00', 'stops': []}
    body = {'days': [day], 'insert': {'day_index': 0, 'stop': stop('new', 14.01)}}
    result = client.post('/api/explore/recalculate', json=body).json()
    assert result['days'][0]['stops'][0]['arrival'] == '08:00'
    day['stops'] = [stop('a', 14), stop('b', 14.02, completed=True)]
    assert client.post('/api/explore/recalculate', json=body).json()['insertion']['stop_index'] == 2
    day['stops'] = [stop('new', 14.01)]
    assert client.post('/api/explore/recalculate', json=body).status_code == 409
    day['stops'] = [stop(str(i), 14) for i in range(40)]
    assert client.post('/api/explore/recalculate', json=body).status_code == 422
    body['insert']['day_index'] = 1
    assert client.post('/api/explore/recalculate', json=body).status_code == 422
    assert client.post('/api/explore/recalculate', json={'days': [{**day, 'start_time': '25:00'}]}).status_code == 422


def test_real_search_adapter_normalizes_provenance_and_caches(client, app):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params['q'] == 'Prague Zoo'
        assert request.url.params['bounded'] == '1'
        return httpx.Response(200, json=[{
            'osm_type': 'way', 'osm_id': 123, 'lat': '50.116', 'lon': '14.41',
            'name': 'Prague Zoo', 'display_name': 'Prague Zoo, Prague, Czechia', 'type': 'zoo',
            'extratags': {'opening_hours': 'Mo-Su 09:00-18:00', 'wikidata': 'Q275630'},
        }, {'osm_type': 'way', 'osm_id': 999, 'lat': '0', 'lon': '0', 'name': 'Far away'}])

    app.state.discovery = DiscoveryProvider(httpx.Client(transport=httpx.MockTransport(handler)))
    url = '/api/explore/places/search?q=Prague%20Zoo&lat=50.08&lng=14.42'
    response = client.get(url)
    assert response.status_code == 200
    items = response.json()['items']
    assert len(items) == 1 and items[0]['id'] == 'way/123'
    assert items[0]['wikidata_id'] == 'Q275630'
    assert items[0]['source_url'] == 'https://www.openstreetmap.org/way/123'
    assert items[0]['hours_text'] == 'Mo-Su 09:00-18:00'
    assert client.get(url).json() == response.json() and len(calls) == 1
    assert client.get('/api/explore/places/search?q=%20%20&lat=50&lng=14').status_code == 422
