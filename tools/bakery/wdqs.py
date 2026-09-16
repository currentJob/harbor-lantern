"""1단계 수확 — WDQS 지리 질의 (DSN-33 · 설계서 §16.4).

**클래스 조인이 없다.** 오사카 질의에 `wdt:P31/wdt:P279*` 제외 조건을 붙이자 3.2초가
48.7초가 됐고, 같은 형태의 파리 질의는 76.0초에 504 였다(실측 2026-09-15 ·
`docs/_recon.md`). 비용의 출처는 지리 검색과 클래스 트리 탐색의 조인이다. 그래서
분류는 2단계 `wbgetentities` 응답을 받아 **파이썬이** 한다(§16.5).

여기서 라벨도 받지 않는다. `wikibase:label` 서비스는 ko 가 없으면 en 으로 **조용히**
폴백해 한국어 라벨 비율을 부풀린다(함정 F16) — 등급이 거짓이 되는 경로다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bakery.io import WDQS_TIMEOUT_S, BakeError, HttpClient, ResponseCache, cache_key

__all__ = ["ENDPOINT", "QUERY_TEMPLATE", "build_query", "fetch_rows", "rows_of"]

ENDPOINT = "https://query.wikidata.org/sparql"

# 안쪽 `SELECT DISTINCT ... LIMIT` 가 항목 단위로 자른다. 바깥 조인이 LIMIT 을 먹으면
# 좌표 문이 여러 개인 항목 때문에 **후보 수가 데이터 형태에 따라 달라진다**
# (다낭에서 손짜산이 3행으로 나왔다 · 실측 · 함정 F17).
# 집계(SAMPLE·MAX)를 쓰지 않는 이유는 따로 있다 — 어느 값이 올지 정의되지 않아
# 같은 입력에 다른 바이트가 나온다(NFR-020 · 함정 F15). 접는 것은 파이썬이 한다(§16.6).
QUERY_TEMPLATE = """# harbor-lantern city guide bake · stage 1 (DSN-33)
# 클래스 조인 없음. 지리 + sitelinks 뿐이다.
SELECT ?item ?sitelinks ?lat ?lng WHERE {
  {
    SELECT DISTINCT ?item ?sitelinks WHERE {
      SERVICE wikibase:around {
        ?item wdt:P625 ?loc .
        bd:serviceParam wikibase:center "Point(%(lng)s %(lat)s)"^^geo:wktLiteral .
        bd:serviceParam wikibase:radius "%(radius_km)s" .
      }
      ?item wikibase:sitelinks ?sitelinks .
      FILTER(?sitelinks >= %(sitelink_min)d)
    }
    ORDER BY DESC(?sitelinks) ?item
    LIMIT %(limit)d
  }
  ?item p:P625/psv:P625 ?node .
  ?node wikibase:geoLatitude ?lat ; wikibase:geoLongitude ?lng .
}
"""


def build_query(city: Mapping[str, Any]) -> str:
    """대장 항목 하나로 질의문을 만든다. **대장 밖의 값은 들어오지 않는다**(DSN-32).

    반경은 km 단위 문자열이다 — `wikibase:radius` 가 km 를 받는다. 대장은 m 로 적으므로
    여기서 한 번만 나눈다(두 곳에서 나누면 한쪽이 조용히 1000배 틀린다).
    """
    center = city["center"]
    return QUERY_TEMPLATE % {
        "lat": f"{float(center['lat']):.6f}",
        "lng": f"{float(center['lng']):.6f}",
        "radius_km": f"{float(city['radius_m']) / 1000.0:g}",
        "sitelink_min": int(city["sitelink_min"]),
        "limit": int(city["limit"]),
    }


def rows_of(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """SPARQL JSON 응답 → 바인딩 행들.

    행을 평탄화하지 않는다. `domain.guide_harvest` 가 공급자 원문 형태
    (`{"item": {"value": "http://www.wikidata.org/entity/Q243"}}`)를 그대로 받는다 —
    파싱이 두 자리로 갈리지 않게 하기 위해서다.
    """
    results = payload.get("results")
    if not isinstance(results, Mapping):
        raise BakeError("WDQS 응답에 results 가 없다")
    bindings = results.get("bindings")
    if not isinstance(bindings, list):
        raise BakeError("WDQS 응답에 bindings 가 없다")
    return [row for row in bindings if isinstance(row, dict)]


def fetch_rows(
    client: HttpClient,
    cache: ResponseCache,
    city: Mapping[str, Any],
    as_of: str,
) -> list[dict[str, Any]]:
    """도시당 1요청. 실패하면 `BakeError` — 호출자가 `query_failed` 로 기록한다.

    파라미터를 바꿔 재시도하지 않는다. 하한은 성능 손잡이가 아니라 선별 기준이고,
    그것을 흔들면 **측정 대상이 측정 과정에 따라 달라진다**(함정 F13).
    """
    query = build_query(city)
    key = cache_key(
        {
            "as_of": as_of,
            "stage": "wdqs",
            "query": query,
        }
    )
    payload = client.cached_json(
        cache,
        str(city["city_id"]),
        "wdqs",
        key,
        lambda: client.post_json(
            "wdqs",
            ENDPOINT,
            data={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
            timeout=WDQS_TIMEOUT_S,
        ),
    )
    return rows_of(payload)
