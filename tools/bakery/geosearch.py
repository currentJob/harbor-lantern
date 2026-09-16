"""1·2단계 수확 — 한국어·영어 위키백과 `geosearch` → `pageprops` (DSN-33 · 설계서 §16.4 v1.5).

**영어로 발견하고, 한국어로 싣는다(v1.6).** 1단계가 `ko.wikipedia` geosearch 하나였을 때,
*한국어 문서는 있는데 그 문서에 좌표가 없는* 장소가 통째로 누락됐다 — geosearch 는 좌표가
붙은 문서만 돌려주기 때문이다. 굽기 실측(2026-09-16)에서 프라하는 ko 31건 · en 500건(상한)
이었고 **en 에만 있으면서 한국어 문서가 존재하는 것이 62건**, 파리는 ko 156 · en 500 에
**50건**이었다. 그 62건에 카를교 · 프라하 천문시계 · 바츨라프 광장 · 흐라드차니 · 루돌피눔이
들어 있었다. 카를교 없는 프라하 가이드는 가이드가 아니다.

그래서 두 위키의 geosearch 를 **합집합**으로 모으되, 싣는 기준은 그대로다 — 위키데이터
`sitelinks` 에 `kowiki` 가 있는 항목만 남긴다(`bake_city_guides.harvest_city`). 영어는
**발견 경로일 뿐** 결과에 들어가지 않는다: 제목도 설명도 항상 한국어 문서에서 온다.
"한국어 100%" 불변식은 위치가 1단계에서 3단계로 옮겨졌을 뿐 그대로다.

**pageid 는 위키마다 다르다.** 그래서 2단계는 **각자 자기 위키에** 묻는다 — en 의 pageid 를
ko 에 물으면 조용히 엉뚱한 문서가 돌아온다(404 가 아니라 다른 문서다).

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
from dataclasses import dataclass
from typing import Any

from bakery.io import BakeError, HttpClient, ResponseCache, cache_key, chunked, round_coord

__all__ = [
    "ENDPOINT",
    "ENDPOINTS",
    "EN_ENDPOINT",
    "GSLIMIT_MAX",
    "GSRADIUS_MAX_M",
    "KO_ENDPOINT",
    "LANGS",
    "PAGEPROPS_BATCH",
    "Union",
    "clamp_radius",
    "discovery_of",
    "endpoint_for",
    "fetch_pages",
    "fetch_wikibase_items",
    "geosearch_params",
    "independent_coord",
    "korean_title",
    "pages_of",
    "qids_of",
    "stage_for",
    "union_by_qid",
]

ENDPOINT = "https://ko.wikipedia.org/w/api.php"  # 기존 이름 — 출처 표기가 이것을 가리킨다
KO_ENDPOINT = ENDPOINT
EN_ENDPOINT = "https://en.wikipedia.org/w/api.php"

# 발견 경로. **순서가 고정이다** — ko 를 먼저 보고 en 을 나중에 본다. 같은 장소가 둘 다에
# 걸렸을 때 어느 쪽 문서 좌표를 쓸지, 보고서의 집계 순서가 어떻게 될지가 여기서 정해진다.
LANGS = ("ko", "en")
ENDPOINTS = {"ko": KO_ENDPOINT, "en": EN_ENDPOINT}

# 단계 이름은 캐시 버킷 파일명이자 요청 집계 키다. ko 쪽은 **이름을 바꾸지 않는다** —
# 바꾸면 이미 받아 둔 한국어 응답이 통째로 무효가 되고, 다시 받을 이유가 없다.
_STAGES = {
    "ko": {"geosearch": "geosearch", "pageprops": "pageprops"},
    "en": {"geosearch": "geosearch-en", "pageprops": "pageprops-en"},
}

# 공급자 고지 상한. 우리가 정하는 값이 아니다.
GSRADIUS_MAX_M = 10000
GSLIMIT_MAX = 500
PAGEPROPS_BATCH = 50


def endpoint_for(lang: str) -> str:
    """`'ko'`·`'en'` → API 주소. 모르는 이름은 **그 자리에서 실패한다**.

    조용히 ko 로 되돌리면 en 사슬이 통째로 ko 를 두 번 긁고, 보고서에는 "en 발견 0건"이
    사실인 것처럼 남는다 — 고장이 데이터처럼 보이는 경로다.
    """
    try:
        return ENDPOINTS[lang]
    except KeyError:
        raise BakeError(f"모르는 위키다: {lang!r} (아는 것은 {'·'.join(LANGS)})") from None


def stage_for(lang: str, step: str) -> str:
    """캐시 버킷·요청 집계에 쓰는 단계 이름. 위키가 다르면 **버킷도 다르다.**"""
    endpoint_for(lang)  # 모르는 위키를 여기서도 막는다
    try:
        return _STAGES[lang][step]
    except KeyError:
        raise BakeError(f"모르는 단계다: {step!r}") from None


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
    lang: str = "ko",
) -> list[dict[str, Any]]:
    """위키 하나당 **1요청**. 실패하면 `BakeError` — 호출자가 `query_failed` 로 기록한다.

    파라미터를 바꿔 재시도하지 않는다. 반경·하한은 성능 손잡이가 아니라 선별 기준이고,
    그것을 흔들면 **측정 대상이 측정 과정에 따라 달라진다**(함정 F13). 두 위키에 **같은**
    중심·반경·`gslimit` 을 던진다 — 한쪽만 넓게 보면 합집합이 무엇을 뜻하는지 말할 수 없다.
    """
    stage = stage_for(lang, "geosearch")
    params = geosearch_params(city)
    key = cache_key({"as_of": as_of, "stage": stage, "params": params})
    payload = client.cached_json(
        cache,
        str(city["city_id"]),
        stage,
        key,
        lambda: client.get_json(stage, endpoint_for(lang), params=params),
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
    lang: str = "ko",
) -> dict[int, str]:
    """pageid **50개씩** → Q-id. `ceil(문서수/50)` 요청 (§16.4 사슬 표 2단계).

    배치는 pageid 오름차순으로 자른다 — 응답 순서와 무관하게 같은 배치가 나와야 캐시가
    같은 키를 얻고, 중단된 굽기를 이어 할 수 있다.

    **`lang` 은 pageid 가 나온 위키여야 한다.** pageid 공간은 위키마다 따로라 en 의 번호를
    ko 에 물으면 오류가 아니라 **다른 문서**가 돌아온다 — 조용히 틀린 Q-id 가 섞인다.
    """
    ordered = sorted({int(pageid) for pageid in pageids})
    stage = stage_for(lang, "pageprops")
    endpoint = endpoint_for(lang)
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
        key = cache_key({"as_of": as_of, "stage": stage, "params": params})
        payload = client.cached_json(
            cache,
            city_id,
            stage,
            key,
            lambda params=params: client.get_json(stage, endpoint, params=params),
        )
        found.update(qids_of(payload))
    return found


# ─────────────────────────────────────────────────────────────────────────
# 합집합 — 전부 순수 함수다 (고정 응답으로 검증 가능 · AC-081 이 베이커 실행을 금한다)
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Union:
    """두 위키의 geosearch 를 Q-id 로 합친 결과.

    `by_qid` 는 `Q-id → 위키 → 문서`이고 **Q-id 오름차순**이다. 어느 위키를 먼저 읽었는지가
    결과 바이트에 스며들면 재현성이 조용히 사라진다(DSN-40).

    `unmapped` 는 위키데이터 항목이 없어 후보가 되지 못한 문서다 — **조용히 빠지지 않는다.**
    """

    by_qid: dict[str, dict[str, dict[str, Any]]]
    unmapped: list[tuple[str, dict[str, Any]]]  # (위키, 문서)
    mapped_rows: int


def union_by_qid(
    pages: Mapping[str, Sequence[Mapping[str, Any]]],
    mappings: Mapping[str, Mapping[int, str]],
) -> Union:
    """`{위키: 문서들}` + `{위키: {pageid: Q-id}}` → Q-id 로 합친 결과. **순수 함수다.**

    **합치는 열쇠는 Q-id 다.** 같은 장소가 두 위키에 다른 제목으로 있어도 항목은 하나이므로
    제목이나 좌표로 맞추려 들 필요가 없다 — 그렇게 맞추면 '한강'처럼 이름이 겹치는 다른
    장소가 섞인다.

    한 위키에서 두 문서가 같은 항목을 가리키면 **중심에 가까운 쪽**을 쓴다(동률이면 pageid).
    공급자 응답 순서에 기대지 않는다.
    """
    by_qid: dict[str, dict[str, dict[str, Any]]] = {}
    unmapped: list[tuple[str, dict[str, Any]]] = []
    mapped_rows = 0
    for lang in LANGS:
        mapping = mappings.get(lang) or {}
        for page in pages.get(lang) or ():
            qid = mapping.get(int(page["pageid"]))
            if not qid:
                unmapped.append((lang, dict(page)))
                continue
            mapped_rows += 1
            slot = by_qid.setdefault(qid, {})
            current = slot.get(lang)
            if current is None or (page["dist"], page["pageid"]) < (current["dist"], current["pageid"]):
                slot[lang] = dict(page)
    return Union({qid: by_qid[qid] for qid in sorted(by_qid)}, unmapped, mapped_rows)


def discovery_of(slot: Mapping[str, Any]) -> str:
    """이 항목이 어느 geosearch 에서 왔는지 — `'ko'` · `'en'` · `'both'` · `''`.

    **`'en'` 은 "영어로만 발견했다"이지 "영어로 싣는다"가 아니다.** 수록까지 가는 항목은
    전부 한국어 문서를 가지고 있다(`korean_title` 관문). 프라하의 카를교가 이 값 `'en'` 이다.
    """
    langs = [lang for lang in LANGS if slot.get(lang)]
    if len(langs) > 1:
        return "both"
    return langs[0] if langs else ""


def korean_title(slot: Mapping[str, Any], kowiki_sitelink: str) -> str:
    """싣는 데 쓸 **한국어 문서 제목**. 없으면 `""` — 그 항목은 싣지 않는다.

    이 한 줄이 "영어로 발견하고 한국어로 싣는다"의 관문이고, **한국어 100% 불변식이 사는
    자리**다(§16.4 v1.6). 1단계가 ko 하나였을 때는 후보가 정의상 한국어 문서였지만, 영어를
    합치면서 그 보장이 1단계에서 이 자리로 옮겨졌다.

    ko geosearch 로 온 항목은 **그 문서 자체가 한국어 문서**이므로 위키데이터 sitelink 가
    낡아 비어 있어도 떨어뜨리지 않는다 — 항목 쪽 색인이 늦는 일이 실제로 있고, 그때 잃는
    것은 우리가 방금 눈으로 본 문서다. en 으로만 온 항목은 `kowiki` sitelink 가 판정한다.
    """
    ko_page = slot.get("ko")
    if isinstance(ko_page, Mapping) and ko_page.get("title"):
        return str(ko_page["title"])
    return str(kowiki_sitelink or "")


def independent_coord(
    own: tuple[float, float],
    page_coords: Mapping[str, tuple[float, float]],
    entity_coord: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """스팟 좌표와 **대조할 수 있는** 다른 좌표. 없으면 `None` (DSN-37 · §16.8).

    후보는 셋이다 — ko 문서 좌표 · en 문서 좌표 · 항목 좌표(`P625`). 이 중 **스팟 좌표와
    값이 같은 것은 건너뛴다**: 자기 자신과 재면 거리가 언제나 0 이고, 그 0 은 "맞다"가 아니라
    **아무것도 재지 않았다**는 뜻이다. 겨울 궁전 사건이 노린 자리가 정확히 그런 통과였다.

    좌표 출처가 셋이 되면서 방어선도 셋이 됐다. 거꾸로 `P625` 가 없어 문서 좌표를 그대로
    스팟 좌표로 쓴 항목은 이제 대조할 것이 없으면 `None` 이 되고, 판정표가 제목만으로
    판단한다(exact 면 통과 · partial 이면 실패) — **완화가 아니라 그 반대다.**
    """
    mine = (round_coord(own[0]), round_coord(own[1]))
    candidates: list[Any] = [page_coords.get(lang) for lang in LANGS]
    candidates.append(entity_coord)
    for coord in candidates:
        if not coord:
            continue
        pair = (float(coord[0]), float(coord[1]))
        if (round_coord(pair[0]), round_coord(pair[1])) == mine:
            continue
        return pair
    return None
