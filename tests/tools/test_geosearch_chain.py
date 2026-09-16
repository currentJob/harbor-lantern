"""수확 사슬 — ko.wikipedia geosearch → pageprops → wbgetentities (DSN-33 · 설계서 §16.4 v1.5).

**여기서 굽지 않는다.** 네트워크는 한 번도 타지 않고, 입력은 전부 이 파일 안의 고정
응답이다(NFR-019 · AC-081). 검증 대상은 사슬의 **순수 함수**와, 그 함수들이 도메인
순수 함수(`guide_harvest.fold_rows` 등)에 넘기는 **입력의 모양**이다.

이 파일이 지키는 명제 다섯:

1. **1단계는 거리순이지 랭킹이 아니다** — 순위는 3단계 `sitelinks` 가 매긴다.
2. **`sitelink_min` 은 질의 필터가 아니라 랭킹 하한이다** — 1단계는 자르지 않는다.
3. **반경 상한(10km)은 조용히 자르지 않는다** — 자른 사실이 문장으로 남는다.
4. **위키데이터 항목이 없는 문서는 조용히 빠지지 않는다.**
5. **SPARQL 이 한 줄도 남아 있지 않다.**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from harbor_lantern.domain.guide_harvest import fold_rows
from harbor_lantern.domain.models import LatLng

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from bakery import geosearch, wikidata  # noqa: E402  (sys.path 조정 뒤에 와야 한다)
from bakery import io as bakery_io  # noqa: E402

REGISTRY = json.loads((TOOLS_DIR / "city-registry.json").read_text(encoding="utf-8"))

# 파리를 본뜬 고정 응답. 값은 실측의 **모양**만 빌렸고 어떤 응답에서도 그대로 오지 않았다.
PARIS_CENTER = LatLng(48.8606, 2.3376)

GEOSEARCH_PAYLOAD: dict[str, Any] = {
    "batchcomplete": True,
    "query": {
        "geosearch": [
            {"pageid": 20, "title": "루브르 박물관", "lat": 48.8606, "lon": 2.3376, "dist": 8.7},
            {"pageid": 30, "title": "파리", "lat": 48.8566, "lon": 2.3522, "dist": 1194.3},
            {"pageid": 40, "title": "생드니", "lat": 48.8600, "lon": 2.3400, "dist": 1800.0},
            {"pageid": 10, "title": "에펠탑", "lat": 48.8584, "lon": 2.2945, "dist": 3184.0},
        ]
    },
}

PAGEPROPS_PAYLOAD: dict[str, Any] = {
    "query": {
        "pages": [
            {"pageid": 10, "title": "에펠탑", "pageprops": {"wikibase_item": "Q243"}},
            {"pageid": 20, "title": "루브르 박물관", "pageprops": {"wikibase_item": "Q19675"}},
            {"pageid": 30, "title": "파리", "pageprops": {"wikibase_item": "Q90"}},
            # 위키데이터 항목이 없는 문서 — `pageprops` 자체가 없다.
            {"pageid": 40, "title": "생드니"},
        ]
    }
}


def _sitelinks(count: int, extra: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """언어판 `count` 개짜리 `sitelinks` 블록. 랭킹의 근거는 **개수**다."""
    links = {
        f"x{index:03d}wiki": {"site": f"x{index:03d}wiki", "title": f"t{index}", "badges": []}
        for index in range(count)
    }
    for site, title in (extra or {}).items():
        links[site] = {"site": site, "title": title, "badges": []}
    return links


def _entity(qid: str, sitelinks: int, p31: list[str], coords: list[tuple[float, float]], ko: str) -> dict[str, Any]:
    return {
        "id": qid,
        "labels": {"ko": {"language": "ko", "value": ko}},
        "sitelinks": _sitelinks(sitelinks - 2, {"kowiki": ko, "enwiki": qid}),
        "claims": {
            "P31": [
                {"mainsnak": {"datavalue": {"value": {"id": cls, "entity-type": "item"}}}} for cls in p31
            ],
            "P625": [
                {"mainsnak": {"datavalue": {"value": {"latitude": lat, "longitude": lng}}}} for lat, lng in coords
            ],
        },
    }


ENTITIES: dict[str, dict[str, Any]] = {
    # 가장 먼 문서인데 가장 유명하다 — 거리순과 랭킹이 갈리는 자리다.
    "Q243": _entity("Q243", 191, ["Q1440476"], [(48.8584, 2.2945)], "에펠탑"),
    "Q19675": _entity("Q19675", 169, ["Q33506"], [(48.8606, 2.3376)], "루브르 박물관"),
    # 도시 자신. 분류에서 제외되지만 **랭킹에서는 1위**다(파리 366 · 실측).
    "Q90": _entity("Q90", 366, ["Q515"], [(48.8566, 2.3522)], "파리"),
}


class _ScriptedClient(bakery_io.HttpClient):
    """고정 응답만 돌려주는 클라이언트. `_request_json` 을 덮어 **네트워크가 닿지 않는다**.

    캐시·배치·요청 집계는 진짜 코드 경로를 그대로 지나간다 — 그래야 "50개씩 자른다"를
    상수 비교가 아니라 실제 호출로 셀 수 있다.
    """

    def __init__(self, responses: dict[str, list[Any]]) -> None:
        super().__init__(offline=False)
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _request_json(self, stage, method, url, *, params, data, headers, timeout):  # type: ignore[no-untyped-def]
        self.calls.append((stage, dict(params or {})))
        self.requests[stage] = self.requests.get(stage, 0) + 1
        return self.responses[stage].pop(0)


# ─────────────────────────────────────────────────────────────────────────
# 1. 반경 상한은 조용히 자르지 않는다
# ─────────────────────────────────────────────────────────────────────────
def test_radius_within_the_cap_is_untouched() -> None:
    radius, note = geosearch.clamp_radius(6000)
    assert radius == 6000
    assert note == ""


def test_radius_above_the_cap_is_clamped_and_says_so() -> None:
    """다낭 15km · 발리 25km 는 실제로 대장에 있다. 자른 사실이 문장으로 남아야 한다.

    조용히 자르면 그 도시만 이유 없이 후보가 적어 보이고, 다음 사람은 "여긴 원래 볼 게
    없나 보다"로 읽는다 — 틀린 결론이 아니라 **확인할 수 없는 결론**이 남는다.
    """
    radius, note = geosearch.clamp_radius(25000)
    assert radius == geosearch.GSRADIUS_MAX_M == 10000
    assert "25,000" in note and "10,000" in note


def test_registry_cities_beyond_the_cap_are_known() -> None:
    """상한을 넘는 도시가 무엇인지 코드가 알고 있어야 한다 — 새로 생기면 이 테스트가 잡는다."""
    beyond = {city["city_id"] for city in REGISTRY["cities"] if city["radius_m"] > geosearch.GSRADIUS_MAX_M}
    assert beyond == {"danang", "bali", "cebu", "nha-trang", "phu-quoc"}
    rules = " ".join(REGISTRY["rules"])
    assert "gsradius" in rules and "10000" in rules


# ─────────────────────────────────────────────────────────────────────────
# 2. 1단계 — 거리순이고, 자르지 않는다
# ─────────────────────────────────────────────────────────────────────────
def test_geosearch_params_are_built_from_the_registry_only() -> None:
    city = {"city_id": "paris", "center": {"lat": 48.8606, "lng": 2.3376}, "radius_m": 6000}
    params = geosearch.geosearch_params(city)
    assert params["list"] == "geosearch"
    assert params["gscoord"] == "48.860600|2.337600"
    assert params["gsradius"] == "6000"
    assert params["formatversion"] == "2"
    # 1단계는 랭킹이 아니므로 자를 이유가 없다 — 상한까지 긁는다(§16.4).
    assert params["gslimit"] == str(geosearch.GSLIMIT_MAX) == "500"


def test_geosearch_params_clamp_the_radius() -> None:
    city = {"city_id": "bali", "center": {"lat": -8.65, "lng": 115.216}, "radius_m": 25000}
    assert geosearch.geosearch_params(city)["gsradius"] == "10000"


def test_pages_of_reads_lon_as_lng_and_orders_by_distance() -> None:
    pages = geosearch.pages_of(GEOSEARCH_PAYLOAD)
    assert [page["pageid"] for page in pages] == [20, 30, 40, 10]  # 거리순
    assert pages[0]["lng"] == 2.3376  # `lon` 이 아니라 `lng` 로 들어온다
    assert all(isinstance(page["pageid"], int) for page in pages)


def test_pages_of_refuses_a_response_it_cannot_read() -> None:
    """빈 목록으로 넘기면 배선 고장과 '장소가 없다'가 구분되지 않는다."""
    with pytest.raises(bakery_io.BakeError):
        geosearch.pages_of({"error": {"code": "badvalue"}})
    with pytest.raises(bakery_io.BakeError):
        geosearch.pages_of({"query": {}})


# ─────────────────────────────────────────────────────────────────────────
# 3. 2단계 — pageid → Q-id, 50개씩
# ─────────────────────────────────────────────────────────────────────────
def test_qids_of_skips_pages_without_a_wikidata_item() -> None:
    mapping = geosearch.qids_of(PAGEPROPS_PAYLOAD)
    assert mapping == {10: "Q243", 20: "Q19675", 30: "Q90"}
    assert 40 not in mapping  # 항목이 없는 문서는 **비워 둔다** — 호출자가 센다


def test_qids_of_also_reads_formatversion_1_shape() -> None:
    legacy = {"query": {"pages": {"10": {"pageid": 10, "pageprops": {"wikibase_item": "Q243"}}}}}
    assert geosearch.qids_of(legacy) == {10: "Q243"}


def test_pageprops_is_batched_at_fifty(tmp_path: Path) -> None:
    """120개 문서면 3요청이다 — 공급자 고지 상한 50 을 우리가 정하지 않는다."""
    pageids = list(range(1, 121))
    responses = [
        {"query": {"pages": [{"pageid": pid, "pageprops": {"wikibase_item": f"Q{pid}"}} for pid in batch]}}
        for batch in (pageids[:50], pageids[50:100], pageids[100:])
    ]
    client = _ScriptedClient({"pageprops": responses})
    cache = bakery_io.ResponseCache(tmp_path)

    found = geosearch.fetch_wikibase_items(client, cache, "paris", pageids, "2026-09-16")

    assert len(client.calls) == 3
    assert [len(call[1]["pageids"].split("|")) for call in client.calls] == [50, 50, 20]
    assert found[1] == "Q1" and found[120] == "Q120"


def test_the_first_stage_is_one_request_per_city(tmp_path: Path) -> None:
    """도시당 1요청. 두 번째 호출은 캐시가 받아 낸다(재개 경로 · §16.18)."""
    city = {"city_id": "paris", "center": {"lat": 48.8606, "lng": 2.3376}, "radius_m": 6000}
    client = _ScriptedClient({"geosearch": [GEOSEARCH_PAYLOAD]})
    cache = bakery_io.ResponseCache(tmp_path)

    first = geosearch.fetch_pages(client, cache, city, "2026-09-16")
    second = geosearch.fetch_pages(client, cache, city, "2026-09-16")

    assert client.requests == {"geosearch": 1}
    assert first == second


# ─────────────────────────────────────────────────────────────────────────
# 4. 3단계 — sitelinks 가 랭킹이고 P625 는 전부 온다
# ─────────────────────────────────────────────────────────────────────────
def test_sitelink_count_is_the_ranking_signal() -> None:
    assert wikidata.sitelink_count(ENTITIES["Q90"]) == 366
    assert wikidata.sitelink_count(ENTITIES["Q243"]) == 191
    assert wikidata.sitelink_count({}) == 0


def test_entities_are_fetched_without_a_sitefilter(tmp_path: Path) -> None:
    """`sitefilter` 를 걸면 언어판이 두 개만 와서 **모든 항목의 중요도가 2 로 평평해진다**.

    랭킹이 사라지는 것이 아니라 거짓이 되는 쪽이라 더 나쁘다 — 순위표는 그대로 나온다.
    """
    client = _ScriptedClient({"wbgetentities": [{"entities": ENTITIES}]})
    cache = bakery_io.ResponseCache(tmp_path)

    wikidata.fetch_entities(client, cache, "paris", ["Q243", "Q90"], "fr", "2026-09-16")

    _stage, params = client.calls[0]
    assert "sitefilter" not in params
    assert params["props"] == "labels|sitelinks|claims"


def test_claim_coords_returns_every_statement() -> None:
    """도쿄 스미다강 2 · 다낭 선짜산 3 — 어느 좌표를 쓸지는 `fold_rows` 가 고른다(§16.6)."""
    many = _entity("Q1", 5, ["Q4022"], [(35.7, 139.8), (35.71, 139.81)], "스미다강")
    assert wikidata.claim_coords(many) == [(35.7, 139.8), (35.71, 139.81)]
    assert wikidata.claim_coord(many) == (35.7, 139.8)
    assert wikidata.claim_coords({}) == []


# ─────────────────────────────────────────────────────────────────────────
# 5. 4단계 — 순위는 거리가 아니라 sitelinks 다
# ─────────────────────────────────────────────────────────────────────────
def _chain_rows() -> list[dict[str, Any]]:
    """베이커가 `fold_rows` 에 넘기는 것과 **같은 모양**으로 행을 만든다(§16.4 4단계)."""
    pages = geosearch.pages_of(GEOSEARCH_PAYLOAD)
    mapping = geosearch.qids_of(PAGEPROPS_PAYLOAD)
    rows: list[dict[str, Any]] = []
    for page in pages:
        qid = mapping.get(page["pageid"])
        if not qid:
            continue
        entity = ENTITIES[qid]
        sitelinks = wikidata.sitelink_count(entity)
        coords = wikidata.claim_coords(entity) or [(page["lat"], page["lng"])]
        rows.extend({"qid": qid, "lat": lat, "lng": lng, "sitelinks": sitelinks} for lat, lng in coords)
    return rows


def test_ranking_comes_from_sitelinks_not_from_distance() -> None:
    """1단계 거리순은 루브르 → 파리 → 에펠탑이고, 랭킹은 파리 → 에펠탑 → 루브르다.

    둘이 갈리는 것이 이 사슬의 핵심이다 — geosearch 에는 유명도 개념이 없다.
    """
    distance_order = [page["title"] for page in geosearch.pages_of(GEOSEARCH_PAYLOAD)]
    assert distance_order[:3] == ["루브르 박물관", "파리", "생드니"]

    folded = fold_rows(_chain_rows(), PARIS_CENTER, 6000.0)
    assert [c.qid for c in folded.candidates] == ["Q90", "Q243", "Q19675"]
    assert [c.sitelinks for c in folded.candidates] == [366, 191, 169]


def test_the_first_stage_does_not_apply_the_sitelink_floor() -> None:
    """하한은 **랭킹 하한**이다 — 1단계 파라미터 어디에도 그 값이 없다(§16.4 v1.5)."""
    city = {"city_id": "paris", "center": {"lat": 48.8606, "lng": 2.3376}, "radius_m": 6000, "sitelink_min": 25}
    params = geosearch.geosearch_params(city)
    assert not any("25" == value for value in params.values())
    assert "sitelink" not in "".join(params)


def test_the_sitelink_floor_is_applied_after_ranking() -> None:
    """하한 200 이면 파리(366)만 남는다 — 거리가 아니라 순위로 자른다."""
    folded = fold_rows(_chain_rows(), PARIS_CENTER, 6000.0)
    kept = [c for c in folded.candidates if c.sitelinks >= 200]
    dropped = [c for c in folded.candidates if c.sitelinks < 200]
    assert [c.qid for c in kept] == ["Q90"]
    assert [c.qid for c in dropped] == ["Q243", "Q19675"]  # 사라지지 않고 보고서로 간다


def test_pages_without_a_wikidata_item_are_countable() -> None:
    """생드니는 후보가 될 수 없다. 그 사실이 **셀 수 있는 형태**로 남아야 한다."""
    pages = geosearch.pages_of(GEOSEARCH_PAYLOAD)
    mapping = geosearch.qids_of(PAGEPROPS_PAYLOAD)
    unmapped = [page["title"] for page in pages if page["pageid"] not in mapping]
    assert unmapped == ["생드니"]
    assert len(pages) - len(mapping) == 1


# ─────────────────────────────────────────────────────────────────────────
# 6. SPARQL 이 한 줄도 남지 않았다
# ─────────────────────────────────────────────────────────────────────────
def test_no_sparql_survives_in_the_bakery() -> None:
    """WDQS 는 HTTP 200 으로 실패한다(함정 F23). 남겨 두면 언젠가 다시 불린다."""
    assert not (TOOLS_DIR / "bakery" / "wdqs.py").exists()
    for path in sorted((TOOLS_DIR / "bakery").glob("*.py")) + [TOOLS_DIR / "bake_city_guides.py"]:
        text = path.read_text(encoding="utf-8")
        assert "SELECT " not in text, f"{path.name} 에 SPARQL 이 남아 있다"
        assert "query.wikidata.org" not in text, f"{path.name} 이 WDQS 를 가리킨다"
        for token in ("wikibase:around", "wdt:P", "psv:P", "bd:serviceParam", "sparql-results"):
            assert token not in text, f"{path.name} 에 SPARQL 조각({token})이 남아 있다"
        # 산문에서 "왜 걷어냈는지"를 설명하는 것은 남기되, **부르는 코드**는 남지 않는다.
        assert "import wdqs" not in text and "wdqs." not in text, f"{path.name} 이 wdqs 모듈을 부른다"


def test_the_chain_endpoints_are_the_ones_the_design_named() -> None:
    assert geosearch.ENDPOINT == "https://ko.wikipedia.org/w/api.php"
    assert wikidata.ENDPOINT == "https://www.wikidata.org/w/api.php"
    assert geosearch.PAGEPROPS_BATCH <= bakery_io.WBGETENTITIES_BATCH == 50


# ─────────────────────────────────────────────────────────────────────────
# 7. 합집합 — 영어로 발견하고, 한국어로 싣는다 (§16.4 v1.6)
# ─────────────────────────────────────────────────────────────────────────
# 프라하를 본뜬 고정 응답. 굽기 실측(2026-09-16)에서 ko geosearch 31건 · en geosearch
# 500건(상한)이었고 **en 에만 있으면서 한국어 문서가 있는 것이 62건**이었다 — 그 안에
# 카를교·프라하 천문시계·바츨라프 광장이 있었다. 한국어 문서는 있는데 **그 문서에 좌표가
# 없어서** ko geosearch 가 돌려주지 않은 것들이다.
#
# 아래 Q-id 와 pageid 는 **모양만 빌린 가짜**다. 어떤 응답에서도 그대로 오지 않았다.
PRAGUE_CENTER = LatLng(50.0875, 14.4213)

KO_PRAGUE: dict[str, Any] = {
    "query": {
        "geosearch": [
            {"pageid": 101, "title": "프라하성", "lat": 50.0900, "lon": 14.4000, "dist": 1600.0},
            {"pageid": 102, "title": "구시가지 광장", "lat": 50.0870, "lon": 14.4210, "dist": 60.0},
        ]
    }
}

EN_PRAGUE: dict[str, Any] = {
    "query": {
        "geosearch": [
            # **ko 의 101 과 같은 번호지만 다른 문서다** — pageid 공간은 위키마다 따로다.
            {"pageid": 101, "title": "Charles Bridge", "lat": 50.0865, "lon": 14.4114, "dist": 710.0},
            {"pageid": 102, "title": "Old Town Square", "lat": 50.0871, "lon": 14.4211, "dist": 62.0},
            {"pageid": 103, "title": "Rudolfinum", "lat": 50.0900, "lon": 14.4160, "dist": 450.0},
            # 한국어 문서가 없는 항목. **여기서 끝나야 한다** — 결과에 들어오면 안 된다.
            {"pageid": 104, "title": "Bench of a Local Poet", "lat": 50.0880, "lon": 14.4230, "dist": 130.0},
            # 위키데이터 항목조차 없는 문서. 조용히 빠지지 않고 세어진다.
            {"pageid": 105, "title": "Unlinked Plaque", "lat": 50.0881, "lon": 14.4231, "dist": 140.0},
        ]
    }
}

KO_PRAGUE_PROPS: dict[str, Any] = {
    "query": {
        "pages": [
            {"pageid": 101, "pageprops": {"wikibase_item": "Q9001"}},
            {"pageid": 102, "pageprops": {"wikibase_item": "Q9002"}},
        ]
    }
}

EN_PRAGUE_PROPS: dict[str, Any] = {
    "query": {
        "pages": [
            {"pageid": 101, "pageprops": {"wikibase_item": "Q9003"}},  # ko 의 101 과 다른 항목
            {"pageid": 102, "pageprops": {"wikibase_item": "Q9002"}},  # 같은 장소 — 여기서 합쳐진다
            {"pageid": 103, "pageprops": {"wikibase_item": "Q9004"}},
            {"pageid": 104, "pageprops": {"wikibase_item": "Q9005"}},
            {"pageid": 105},
        ]
    }
}


def _prague_entity(qid: str, sitelinks: int, ko_title: str, coords: list[tuple[float, float]]) -> dict[str, Any]:
    """`ko_title` 이 빈 문자열이면 **`kowiki` sitelink 가 없는 항목**이다."""
    extra = {"enwiki": f"en:{qid}"}
    if ko_title:
        extra["kowiki"] = ko_title
    return {
        "id": qid,
        "labels": {"ko": {"language": "ko", "value": ko_title or qid}},
        "sitelinks": _sitelinks(sitelinks - len(extra), extra),
        "claims": {
            "P625": [
                {"mainsnak": {"datavalue": {"value": {"latitude": lat, "longitude": lng}}}} for lat, lng in coords
            ]
        },
    }


PRAGUE_ENTITIES: dict[str, dict[str, Any]] = {
    "Q9001": _prague_entity("Q9001", 120, "프라하성", [(50.0900, 14.4000)]),
    "Q9002": _prague_entity("Q9002", 90, "구시가지 광장", [(50.0870, 14.4210)]),
    # 카를교 — 한국어 문서는 있는데 그 문서에 좌표가 없어 ko geosearch 에 안 잡혔다.
    "Q9003": _prague_entity("Q9003", 110, "카를교", [(50.0865, 14.4114)]),
    # 루돌피눔 — 같은 사연. `P625` 도 없어 문서 좌표(en)로 내려간다.
    "Q9004": _prague_entity("Q9004", 45, "루돌피눔", []),
    # 한국어 문서가 **없는** 항목. 이것이 결과에 들어오면 "한국어 100%" 가 깨진다.
    "Q9005": _prague_entity("Q9005", 6, "", [(50.0880, 14.4230)]),
}


def _prague_union() -> geosearch.Union:
    return geosearch.union_by_qid(
        {"ko": geosearch.pages_of(KO_PRAGUE), "en": geosearch.pages_of(EN_PRAGUE)},
        {"ko": geosearch.qids_of(KO_PRAGUE_PROPS), "en": geosearch.qids_of(EN_PRAGUE_PROPS)},
    )


def _gate(union: geosearch.Union, qid: str) -> str:
    """베이커가 3단계 뒤에 세우는 관문과 **같은 호출**이다."""
    return geosearch.korean_title(union.by_qid[qid], wikidata.sitelink_title(PRAGUE_ENTITIES[qid], "kowiki"))


def test_each_wiki_is_asked_its_own_endpoint_and_bucket(tmp_path: Path) -> None:
    """**pageid 는 위키마다 다르다.** en 의 번호를 ko 에 물으면 오류가 아니라 다른 문서가 온다.

    ko 쪽 단계 이름은 바뀌지 않는다 — 바뀌면 이미 받아 둔 한국어 응답이 통째로 무효가 된다.
    """
    assert geosearch.endpoint_for("ko") == "https://ko.wikipedia.org/w/api.php"
    assert geosearch.endpoint_for("en") == "https://en.wikipedia.org/w/api.php"
    assert geosearch.stage_for("ko", "geosearch") == "geosearch"
    assert geosearch.stage_for("ko", "pageprops") == "pageprops"
    assert geosearch.stage_for("en", "geosearch") != geosearch.stage_for("ko", "geosearch")
    assert geosearch.stage_for("en", "pageprops") != geosearch.stage_for("ko", "pageprops")

    city = {"city_id": "prague", "center": {"lat": 50.0875, "lng": 14.4213}, "radius_m": 6000}
    client = _ScriptedClient({"geosearch": [KO_PRAGUE], "geosearch-en": [EN_PRAGUE]})
    cache = bakery_io.ResponseCache(tmp_path)

    ko = geosearch.fetch_pages(client, cache, city, "2026-09-16", "ko")
    en = geosearch.fetch_pages(client, cache, city, "2026-09-16", "en")
    # 두 번째 호출은 캐시가 받아 낸다 — 위키별로 버킷이 갈려 있어야 성립한다(재개 경로).
    assert geosearch.fetch_pages(client, cache, city, "2026-09-16", "ko") == ko
    assert geosearch.fetch_pages(client, cache, city, "2026-09-16", "en") == en

    assert client.requests == {"geosearch": 1, "geosearch-en": 1}
    assert len(ko) == 2
    assert len(en) == 5
    assert (tmp_path / "prague" / "geosearch.json").exists()
    assert (tmp_path / "prague" / "geosearch-en.json").exists()


def test_an_unknown_wiki_fails_instead_of_falling_back() -> None:
    """조용히 ko 로 되돌리면 ko 를 두 번 긁고 '영어 발견 0건'이 사실처럼 남는다."""
    with pytest.raises(bakery_io.BakeError):
        geosearch.endpoint_for("de")
    with pytest.raises(bakery_io.BakeError):
        geosearch.stage_for("de", "geosearch")


def test_pageprops_asks_the_wiki_the_pageids_came_from(tmp_path: Path) -> None:
    client = _ScriptedClient({"pageprops-en": [EN_PRAGUE_PROPS]})
    cache = bakery_io.ResponseCache(tmp_path)

    found = geosearch.fetch_wikibase_items(client, cache, "prague", [101, 102, 103, 104, 105], "2026-09-16", "en")

    assert client.requests == {"pageprops-en": 1}
    assert found[101] == "Q9003"  # ko 의 101(Q9001)이 아니다
    assert 105 not in found


def test_the_union_is_keyed_by_qid_and_ordered() -> None:
    """합집합의 열쇠는 Q-id 다 — 같은 장소가 두 위키에 다른 제목으로 있어도 항목은 하나다.

    순서는 **Q-id 오름차순**으로 고정한다. 어느 위키를 먼저 읽었는지가 결과 바이트에
    스며들면 재현성이 조용히 사라진다(DSN-40 · AC-082).
    """
    union = _prague_union()
    assert list(union.by_qid) == ["Q9001", "Q9002", "Q9003", "Q9004", "Q9005"]
    assert union.mapped_rows == 6  # ko 2 + en 4 (105 는 위키데이터 항목이 없다)
    assert [(lang, page["title"]) for lang, page in union.unmapped] == [("en", "Unlinked Plaque")]

    assert geosearch.discovery_of(union.by_qid["Q9001"]) == "ko"
    assert geosearch.discovery_of(union.by_qid["Q9002"]) == "both"
    assert geosearch.discovery_of(union.by_qid["Q9003"]) == "en"
    assert geosearch.discovery_of({}) == ""


def test_the_union_order_does_not_depend_on_which_wiki_was_read_first() -> None:
    forward = _prague_union()
    backward = geosearch.union_by_qid(
        {"en": geosearch.pages_of(EN_PRAGUE), "ko": geosearch.pages_of(KO_PRAGUE)},
        {"en": geosearch.qids_of(EN_PRAGUE_PROPS), "ko": geosearch.qids_of(KO_PRAGUE_PROPS)},
    )
    assert list(forward.by_qid) == list(backward.by_qid)
    assert forward.by_qid == backward.by_qid


def test_two_documents_of_one_wiki_fold_to_the_nearest() -> None:
    """한 위키에서 두 문서가 같은 항목을 가리키면 중심에 가까운 쪽을 쓴다(동률이면 pageid)."""
    pages = {
        "ko": [
            {"pageid": 7, "title": "먼 쪽", "lat": 1.0, "lng": 1.0, "dist": 900.0},
            {"pageid": 8, "title": "가까운 쪽", "lat": 1.1, "lng": 1.1, "dist": 10.0},
        ],
        "en": [],
    }
    union = geosearch.union_by_qid(pages, {"ko": {7: "Q1", 8: "Q1"}, "en": {}})
    assert union.by_qid["Q1"]["ko"]["title"] == "가까운 쪽"
    assert union.mapped_rows == 2


# ── 이 절의 핵심 명제 ───────────────────────────────────────────────────────
def test_an_english_only_item_without_a_korean_article_never_gets_in() -> None:
    """**한국어 문서가 없으면 싣지 않는다** — 영어는 발견 경로일 뿐이다(§16.4 v1.6).

    이 관문이 "한국어 100%" 불변식이 사는 자리다. 1단계가 ko 하나였을 때는 후보가 정의상
    한국어 문서였지만, 영어를 합치면서 그 보장이 1단계에서 이 자리로 옮겨졌다. 여기가
    풀리면 화면에 **영어 이름 + 빈 설명**이 실린다.
    """
    union = _prague_union()
    assert _gate(union, "Q9005") == ""  # en 으로만 발견 + kowiki 없음 → 탈락
    assert _gate(union, "Q9003") == "카를교"  # en 으로만 발견 + kowiki 있음 → 그 제목으로 싣는다
    assert _gate(union, "Q9004") == "루돌피눔"
    assert _gate(union, "Q9001") == "프라하성"  # ko 로 발견 → 1단계가 이미 제목을 들고 있다


def test_a_korean_page_carries_the_item_even_if_the_sitelink_is_stale() -> None:
    """ko geosearch 로 온 항목은 **그 문서 자체가 한국어 문서**다.

    위키데이터 쪽 색인이 늦어 `kowiki` sitelink 가 비어 있는 일이 있고, 그때 잃는 것은 방금
    눈으로 본 문서다 — 관문이 그것까지 떨어뜨리면 안 된다.
    """
    slot = {"ko": {"pageid": 1, "title": "구시가지 광장", "lat": 1.0, "lng": 1.0, "dist": 1.0}}
    assert geosearch.korean_title(slot, "") == "구시가지 광장"
    assert geosearch.korean_title({}, "") == ""


def test_the_english_route_adds_places_the_korean_search_cannot_see() -> None:
    """합집합이 값을 하는지 — 카를교가 들어오고, 한국어 문서 없는 항목은 안 들어온다.

    ko 만 보면 후보가 둘이다. 영어를 합치면 넷이 되고 그 둘이 **한국어 문서를 가진 장소**다.
    카를교 없는 프라하 가이드는 가이드가 아니다.
    """
    union = _prague_union()
    rows: list[dict[str, Any]] = []
    kept: list[str] = []
    for qid, slot in union.by_qid.items():
        if not _gate(union, qid):
            continue
        entity = PRAGUE_ENTITIES[qid]
        kept.append(qid)
        coords = wikidata.claim_coords(entity)
        if not coords:
            page = slot.get("ko") or slot.get("en")
            coords = [(page["lat"], page["lng"])]
        rows.extend(
            {"qid": qid, "lat": lat, "lng": lng, "sitelinks": wikidata.sitelink_count(entity)}
            for lat, lng in coords
        )

    ko_only = [qid for qid, slot in union.by_qid.items() if geosearch.discovery_of(slot) == "ko"]
    assert len(ko_only) == 1  # ko geosearch 단독으로는 프라하성 하나뿐이다
    assert kept == ["Q9001", "Q9002", "Q9003", "Q9004"]
    assert "Q9005" not in kept

    folded = fold_rows(rows, PRAGUE_CENTER, 6000.0)
    assert [c.qid for c in folded.candidates] == ["Q9001", "Q9003", "Q9002", "Q9004"]
    # 루돌피눔은 `P625` 가 없어 **en 문서 좌표**로 내려왔다 — 반경 검사를 그대로 통과한다.
    rudolfinum = next(c for c in folded.candidates if c.qid == "Q9004")
    assert (rudolfinum.coord.lat, rudolfinum.coord.lng) == (50.0900, 14.4160)


# ── 좌표 출처가 셋이면 대조도 셋이다 ────────────────────────────────────────
def test_the_cross_check_never_compares_a_coordinate_with_itself() -> None:
    """자기 자신과 재면 거리가 언제나 0 이고, 그 0 은 '맞다'가 아니라 **아무것도 재지 않았다**다.

    겨울 궁전 사건(다른 항목의 설명이 붙는 것)이 노린 자리가 정확히 그런 통과였다.
    """
    own = (50.0865, 14.4114)
    assert geosearch.independent_coord(own, {"en": own}, None) is None
    assert geosearch.independent_coord(own, {"en": own}, (50.09, 14.42)) == (50.09, 14.42)


def test_the_cross_check_prefers_the_korean_page_then_english_then_p625() -> None:
    own = (50.0, 14.0)
    ko, en, p625 = (50.01, 14.01), (50.02, 14.02), (50.03, 14.03)
    assert geosearch.independent_coord(own, {"ko": ko, "en": en}, p625) == ko
    assert geosearch.independent_coord(own, {"en": en}, p625) == en
    assert geosearch.independent_coord(own, {}, p625) == p625
    assert geosearch.independent_coord(own, {}, None) is None


def test_the_cross_check_ignores_rounding_of_the_spot_coordinate() -> None:
    """스팟 좌표는 소수 6자리로 반올림돼 저장된다 — 같은 값인지도 그 자리에서 본다."""
    own = (50.086512, 14.411434)
    assert geosearch.independent_coord(own, {"en": (50.0865124, 14.4114338)}, None) is None
