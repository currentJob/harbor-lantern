"""Research-time aggregate ratings; no runtime MCP dependency or review text storage."""

import json
from functools import lru_cache
from pathlib import Path

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng
from harbor_lantern.domain.review_plan import review_score
from harbor_lantern.services.external.discovery import in_bounds
from harbor_lantern.services.guides import load_city, load_index

DATA = Path(__file__).resolve().parents[3] / 'seed' / 'place-ratings.json'


@lru_cache(maxsize=1)
def rating_catalog():
    if not DATA.exists():
        return {}
    return json.loads(DATA.read_text(encoding='utf-8')).get('cities', {})


def city_ratings(city_id):
    return {key: row for key, row in rating_catalog().get(city_id, {}).items()
            if review_score(row) is not None and row.get('source_url') and row.get('fetched_at')}


def rated_spots(city_id, spots):
    ratings = city_ratings(city_id)
    return [{**spot, **({'review': ratings[spot['id']]} if spot['id'] in ratings else {})} for spot in spots]


@lru_cache(maxsize=1)
def viewport_catalog(path=None):
    # This endpoint searches across cities; existing city-list endpoints remain index-only.
    return tuple(spot for city in load_index(path).cities
                 if (guide := load_city(city['city_id'], path))
                 for spot in rated_spots(city['city_id'], guide.spots))


def enrich_viewport(items, bounds, path=None):
    guides = [p for p in viewport_catalog(path) if in_bounds(p, bounds)]
    result = {p['id']: dict(p) for p in guides}
    for place in items:
        if not in_bounds(place, bounds):
            continue
        match = next((g for g in guides if (
            place.get('wikidata_id') and place['wikidata_id'] == (g.get('wikidata_id') or g['id'].removeprefix('wd:'))
        ) or (place['name'].casefold() in {g['name'].casefold(), g.get('name_original', '').casefold()}
              and haversine_m(LatLng(place['lat'], place['lng']), LatLng(g['lat'], g['lng'])) < 50)), None)
        merged = {**(match or {}), **place}
        if match:
            merged['id'] = match['id']
            if match.get('description'):
                merged['description'] = match['description']
                merged['description_source'] = match.get('description_source')
            merged['tips'] = [*match.get('tips', []), *place.get('tips', [])]
        result[merged['id']] = merged
    return list(result.values())
