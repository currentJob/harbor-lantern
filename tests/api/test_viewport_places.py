"""Viewport discovery: world coordinates, bounded requests, honest partial data and rating identity."""
import httpx
import pytest

from harbor_lantern.services import ratings
from harbor_lantern.services.external.discovery import DiscoveryProvider
from harbor_lantern.services.external.ports import ExternalUnavailable


def test_overpass_viewport_preserves_facts_caches_and_reports_limit():
    calls = []
    row = {'type': 'node', 'id': 1, 'lat': 22.19, 'lon': 113.54,
           'tags': {'name': 'Museum', 'tourism': 'museum', 'description': 'Published description',
                    'fee': 'no', 'wheelchair': 'limited', 'opening_hours': 'Tu-Su 10:00-18:00',
                    'website': 'javascript:alert(1)', 'wikidata': 'Q1', 'stars': '5'}}

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'elements': [row, {**row, 'id': 2, 'lat': 35},
                                                     {**row, 'id': 3, 'lat': 'nan'}] + [row]*498})

    provider = DiscoveryProvider(httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.viewport((22.18, 113.53, 22.20, 113.55))
    assert result['truncated'] and result['limit'] == 500
    assert len(result['items']) == 1
    place = result['items'][0]
    assert place['description'] == 'Published description'
    assert place['description_source']['license'] == 'ODbL'
    assert len(place['tips']) == 3
    assert '무료' in place['tips'][1]['text']
    assert place['website'] is None and 'review' not in place and 'rating' not in place
    assert place['wikidata_id'] == 'Q1'
    assert all('source_url' in tip and 'evidence' in tip for tip in place['tips'])
    place['name'] = 'mutated'
    assert provider.viewport((22.18, 113.53, 22.20, 113.55))['items'][0]['name'] == 'Museum'
    assert len(calls) == 1
    body = calls[0].content.decode()
    assert 'nominatim' not in str(calls[0].url) and '501' in body


def test_viewport_merges_identity_not_unrelated_ratings(monkeypatch):
    guide = {'id': 'wd:Q1', 'name': 'Museum', 'lat': 22.19, 'lng': 113.54,
             'description': 'Verified', 'description_source': {'url': 'https://example.org'},
             'review': {'source': 'Trip.com', 'rating': 4.8, 'review_count': 1000}, 'tips': []}
    monkeypatch.setattr(ratings, 'viewport_catalog', lambda path: (guide,))
    live = {'id': 'node/1', 'name': 'Museum local', 'wikidata_id': 'Q1',
            'lat': 22.19, 'lng': 113.54, 'source': 'OpenStreetMap', 'description': '', 'tips': []}
    bounds = (22.18, 113.53, 22.20, 113.55)
    result = ratings.enrich_viewport([live, {**live, 'id': 'node/2', 'wikidata_id': 'Q2'}], bounds)
    assert len(result) == 2
    assert result[0]['review'] == guide['review'] and result[0]['description'] == 'Verified'
    assert 'review' not in result[1]
    assert guide['name'] == 'Museum'


def test_viewport_api_crosses_dateline_and_is_not_city_limited(app, client, monkeypatch):
    monkeypatch.setattr(ratings, 'viewport_catalog', lambda path: ())
    received = []

    class Provider:
        def viewport(self, bounds, category='all'):
            received.append(bounds)
            return {'items': [{'id': 'node/1', 'name': 'East', 'lat': 0, 'lng': 179.99},
                              {'id': 'node/2', 'name': 'West', 'lat': 0, 'lng': -179.99}],
                    'limit': 500, 'truncated': False}

    app.state.discovery = Provider()
    response = client.get('/api/explore/places/viewport?south=-.01&west=179.98&north=.01&east=-179.98')
    assert response.status_code == 200 and len(response.json()['items']) == 2
    assert received == [(-.01, 179.98, .01, -179.98)]
    assert not response.json()['partial']


@pytest.mark.parametrize('bounds', [
    'south=nan&west=0&north=1&east=1', 'south=0&west=0&north=91&east=1',
    'south=1&west=0&north=0&east=1', 'south=0&west=-180&north=1&east=180',
    'south=0&west=0&north=1&east=1', 'south=0&west=0&north=0&east=0',
])
def test_viewport_rejects_invalid_or_world_size_bounds(client, bounds):
    assert client.get('/api/explore/places/viewport?'+bounds).status_code == 422


def test_viewport_outage_is_explicit_without_nominatim_fallback(app, client, monkeypatch):
    monkeypatch.setattr(ratings, 'viewport_catalog', lambda path: ())

    class Unavailable:
        def viewport(self, bounds, category='all'):
            raise ExternalUnavailable('Temporary outage')

    app.state.discovery = Unavailable()
    response = client.get('/api/explore/places/viewport?south=0&west=0&north=.01&east=.01')
    assert response.status_code == 200
    assert response.json()['partial'] and response.json()['items'] == []
    assert 'Temporary outage' in response.json()['notice']
