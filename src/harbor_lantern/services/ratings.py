"""Research-time aggregate ratings; no runtime MCP dependency or review text storage."""

import json
from functools import lru_cache
from pathlib import Path

from harbor_lantern.domain.review_plan import review_score

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
