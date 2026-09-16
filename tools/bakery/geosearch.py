"""1·2단계 수확 — 한국어 위키백과 `geosearch` → `pageprops` (DSN-33 · 설계서 §16.4 v1.5).

**SPARQL 이 없다.** 이 자리에는 원래 WDQS 질의가 있었고, T8 에서 실제로 구우며 세 가지가
차례로 드러나 통째로 걷어냈다(실측 2026-09-16 · `docs/_recon.md` "실측 2"):

1. WDQS 는 **HTTP 200 으로 실패한다** — 내부 상한을 넘기면 부분 JSON 뒤에 Java 스택
   트레이스를 붙인다(파리 62,770 bytes · 61.5초). 재시도 판정에 안 걸리고 `JSONDecodeError`
   로 위장한다(함정 F23).
2. 429 가 시작된 뒤의 측정은 질의가 아니라 **내 요청량을 잰 것**이다(함정 F24).
3. 식힌 뒤에도 파리는 `LIMIT` 을 낮춰도 60초를 못 지켰다.

대체 사슬은 파리 1단계 **1.0초**, 사슬 전체 16.3초·9요청, Q-id 매핑 173/173,
35개 도시 조사 41초다(실측 2026-09-16).

**1단계는 거리순이다. 랭킹이 아니다.** geosearch 에는 유명도 개념이 없으므로 여기서는
자르지 않고 `gslimit` 상한까지 긁는다 — 순위는 3단계 `sitelinks` 가 매긴다(§16.4).
1.0~1.6초짜리 요청 하나라 넓게 긁는 비용이 없다.

`gsradius` 상한 10km · `gslimit` 상한 500 · `pageprops` 배치 50 은 전부 공급자 고지다
(https://www.mediawiki.org/wiki/API:Geosearch · https://www.mediawiki.org/wiki/API:Pageprops
· 2026-09-16 조회).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bakery.io import BakeError, HttpClient, ResponseCache, cache_key, chunked

__all__ = [
    "ENDPOINT",
    "GSLIMIT_MAX",
    "GSRADIUS_MAX_M",
    "PAGEPROPS_BATCH",
    "clamp_radius",
    "fetch_pages",
    "fetch_wikibase_items",
    "geosearch_params",
    "pages_of",
    "qids_of",
]

ENDPOINT = "https://ko.wikipedia.org/w/api.php"

# 공급자 고지 상한. 우리가 정하는 값이 아니다.
GSRADIUS_MAX_M = 10000
GSLIMIT_MAX = 500
PAGEPROPS_BATCH = 50


def clamp_radius(radius_m: float) -> tuple[int, str]:
    """`gsradius` 상한(10km)으로 자르고 **자른 사실을 문장으로 함께 돌려준다**.

    조용히 자르면 그 도시만 이유 없이 후보가 적어 보인다 — 다낭(15km)·발리(25km) 처럼
    대장이 상한보다 넓게 적힌 도시가 실제로 있다. 자른 사실은 `known_gaps` 와 굽기
    보고서에 그대로 실린다(AC-084 의 "조용히 빠지지 않는다"와 같은 규칙).

    더 넓게 보려면 중심을 나눠 여러 번 부르면 되지만(§16.4), 그 선택은 사람이 대장에서
    한다 — 베이커가 중심을 지어내지 않는다.
    """
    requested = int(round(float(radius_m)))
    if requested <= GSRADIUS_MAX_M:
        return requested, ""
    return GSRADIUS_MAX_M, (
        f"대장의 반경 {requested:,}m 는 geosearch 상한 {GSRADIUS_MAX_M:,}m 를 넘어 "
        f"{GSRADIUS_MAX_M:,}m 로 잘렸다 — 그 바깥의 장소는 이번 수확에 들어오지 않았다."
    )


def geosearch_params(city: Mapping[str, Any]) -> dict[str, str]:
    """대장 항목 하나로 1단계 파라미터를 만든다. **대장 밖의 값은 들어오지 않는다**(DSN-32).

    `gscoord` 는 `위도|경도` 한 문자열이다. 좌표를 소수 6자리로 고정하는 이유는
    캐시 키가 부동소수 표기에 따라 갈리지 않게 하기 위해서다(NFR-020).
    """
    center = city["center"]
    radius, _note = clamp_radius(float(city["radius_m"]))
    return {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "geosearch",
        "gscoord": f"{float(center['lat']):.6f}|{float(center['lng']):.6f}",
        "gsradius": str(radius),
        "gslimit": str(GSLIMIT_MAX),
    }


def pages_of(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """geosearch 응답 → `{pageid, title, lat, lng, dist}` 목록. **순수 함수다.**

    `lon` 을 `lng` 로 바꿔 담는다 — 이 저장소의 나머지 전부가 `lng` 이고, 두 표기가
    섞이면 어느 자리에서 조용히 `None` 이 되는지 찾기 어려워진다.

    응답 형태가 아예 다르면 빈 목록이 아니라 `BakeError` 다. 빈 목록으로 넘기면 그 도시가
    "후보 0건"으로 기록되고, 배선 고장과 실제로 장소가 없는 것이 구분되지 않는다.
    """
    query = payload.get("query") if isinstance(payload, Mapping) else None
    if not isinstance(query, Mapping):
        raise BakeError("geosearch 응답에 query 가 없다")
    rows = query.get("geosearch")
    if not isinstance(rows, list):
        raise BakeError("geosearch 응답에 geosearch 목록이 없다")

    pages: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        pageid = row.get("pageid")
        lat, lon = row.get("lat"), row.get("lon")
        if pageid is None or lat is None or lon is None:
            continue
        pages.append(
            {
                "pageid": int(pageid),
                "title": str(row.get("title") or ""),
                "lat": float(lat),
                "lng": float(lon),
                "dist": float(row.get("dist") or 0.0),
            }
        )
    # 거리순으로 고정한다. 공급자가 이미 거리순으로 주지만, 순서를 여기서 정해 두면
    # 응답 순서가 바뀌어도 같은 입력에 같은 바이트가 나온다(NFR-020).
    pages.sort(key=lambda page: (page["dist"], page["pageid"]))
    return pages


def fetch_pages(
    client: HttpClient,
    cache: ResponseCache,
    city: Mapping[str, Any],
    as_of: str,
) -> list[dict[str, Any]]:
    """도시당 **1요청**. 실패하면 `BakeError` — 호출자가 `query_failed` 로 기록한다.

    파라미터를 바꿔 재시도하지 않는다. 반경·하한은 성능 손잡이가 아니라 선별 기준이고,
    그것을 흔들면 **측정 대상이 측정 과정에 따라 달라진다**(함정 F13).
    """
    params = geosearch_params(city)
    key = cache_key({"as_of": as_of, "stage": "geosearch", "params": params})
    payload = client.cached_json(
        cache,
        str(city["city_id"]),
        "geosearch",
        key,
        lambda: client.get_json("geosearch", ENDPOINT, params=params),
    )
    return pages_of(payload)


def qids_of(payload: Mapping[str, Any]) -> dict[int, str]:
    """`pageprops` 응답 → `pageid → Q-id`. **순수 함수다.**

    위키데이터 항목이 없는 문서는 `wikibase_item` 이 없다 — 그 자리는 **비워 둔다**.
    호출자가 "매핑되지 않은 문서"로 세어 보고서에 남긴다(조용히 빠지지 않는다).
    """
    query = payload.get("query") if isinstance(payload, Mapping) else None
    if not isinstance(query, Mapping):
        return {}
    pages = query.get("pages")
    rows: list[Any] = []
    if isinstance(pages, list):
        rows = list(pages)
    elif isinstance(pages, Mapping):  # formatversion=1 응답도 읽는다
        rows = list(pages.values())

    found: dict[int, str] = {}
    for page in rows:
        if not isinstance(page, Mapping) or page.get("pageid") is None:
            continue
        props = page.get("pageprops")
        if not isinstance(props, Mapping):
            continue
        qid = str(props.get("wikibase_item") or "")
        if qid:
            found[int(page["pageid"])] = qid
    return found


def fetch_wikibase_items(
    client: HttpClient,
    cache: ResponseCache,
    city_id: str,
    pageids: Sequence[int],
    as_of: str,
) -> dict[int, str]:
    """pageid **50개씩** → Q-id. `ceil(문서수/50)` 요청 (§16.4 사슬 표 2단계).

    배치는 pageid 오름차순으로 자른다 — 응답 순서와 무관하게 같은 배치가 나와야 캐시가
    같은 키를 얻고, 중단된 굽기를 이어 할 수 있다.
    """
    ordered = sorted({int(pageid) for pageid in pageids})
    found: dict[int, str] = {}
    for batch in chunked(ordered, PAGEPROPS_BATCH):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "pageids": "|".join(str(pageid) for pageid in batch),
            "prop": "pageprops",
            "ppprop": "wikibase_item",
        }
        key = cache_key({"as_of": as_of, "stage": "pageprops", "params": params})
        payload = client.cached_json(
            cache,
            city_id,
            "pageprops",
            key,
            lambda params=params: client.get_json("pageprops", ENDPOINT, params=params),
        )
        found.update(qids_of(payload))
    return found
