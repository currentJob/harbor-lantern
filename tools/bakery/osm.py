"""4단계 — Overpass 영업시간·입장료 (DSN-36 4단계 · DSN-39 · §16.7 · §16.10).

**이름으로 맞추지 않는다. `wikidata` 태그의 QID 로 맞춘다.** 이름 매칭이 어떻게 조용히
틀리는지는 이 저장소에 실물 기록이 있다 — 미쉐린 좌표 작업에서 `Duddell's` 가 공항
지점으로, `Amber` 가 타이항의 동명 가게로 잡혔다. QID 는 그 실패 양식이 구조적으로 없다.

좌표는 **가져오지 않는다**. 좌표의 진실은 위키데이터 쪽이고, 두 출처를 섞으면 어느 것이
검증 대상인지 흐려진다. 얻는 것은 `opening_hours`·`fee`·`website` 와 요소 URL 뿐이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bakery.io import HttpClient, ResponseCache, cache_key

__all__ = ["ENDPOINTS", "LICENSE", "PROVIDER", "build_query", "derive_tips", "facts_of", "fetch_facts"]

# `tools/resolve_curated.py` 가 쓰는 것과 같은 엔드포인트 순서다.
ENDPOINTS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
PROVIDER = "OpenStreetMap"
LICENSE = "ODbL 1.0"

# `out tags center` 순서로 쓴다(설계서 §16.7 의 예시는 `out center tags` 로 적혀 있는데,
# Overpass QL 은 출력 상세도(tags) 를 먼저, 기하(center) 를 뒤에 받는다). 태그가 목적이고
# center 는 요소 타입이 way/relation 일 때 응답이 비지 않게 하는 안전장치다.
QUERY_TEMPLATE = """[out:json][timeout:60];
nwr(around:%(radius)d,%(lat)s,%(lng)s)["wikidata"];
out tags center 2000;
"""


def build_query(city: Mapping[str, Any]) -> str:
    center = city["center"]
    return QUERY_TEMPLATE % {
        "radius": int(city["radius_m"]),
        "lat": f"{float(center['lat']):.6f}",
        "lng": f"{float(center['lng']):.6f}",
    }


def facts_of(payload: Mapping[str, Any], wanted: Sequence[str]) -> dict[str, dict[str, str]]:
    """Overpass 응답 → `QID → {opening_hours, fee, website, url}`. **순수 함수다.**

    우리 후보 집합에 없는 QID 는 전부 버린다. 같은 QID 가 여러 요소에 붙어 있으면
    `(요소 타입, id)` 정렬로 **첫 요소**를 쓴다 — 어느 것이 올지가 응답 순서에 따라
    달라지면 같은 입력에 다른 바이트가 나온다(NFR-020).
    """
    keep = set(wanted)
    elements = payload.get("elements") if isinstance(payload, Mapping) else None
    rows: list[tuple[str, str, int, dict[str, str]]] = []
    if isinstance(elements, list):
        for element in elements:
            if not isinstance(element, Mapping):
                continue
            tags = element.get("tags")
            if not isinstance(tags, Mapping):
                continue
            qid = str(tags.get("wikidata") or "")
            if qid not in keep:
                continue
            kind = str(element.get("type") or "node")
            osm_id = int(element.get("id") or 0)
            fact = {
                "opening_hours": str(tags.get("opening_hours") or ""),
                "fee": str(tags.get("fee") or tags.get("charge") or ""),
                "website": str(tags.get("website") or ""),
                "url": f"https://www.openstreetmap.org/{kind}/{osm_id}",
            }
            rows.append((qid, kind, osm_id, fact))

    found: dict[str, dict[str, str]] = {}
    for qid, _kind, _osm_id, fact in sorted(rows, key=lambda row: (row[0], row[1], row[2])):
        found.setdefault(qid, fact)
    return found


def fetch_facts(
    client: HttpClient,
    cache: ResponseCache,
    city: Mapping[str, Any],
    qids: Sequence[str],
    as_of: str,
) -> dict[str, dict[str, str]]:
    """도시당 1요청. 실패해도 굽기를 멈추지 않는다 — 영업시간은 없으면 없는 대로 둔다.

    설명(REQ-024)과 달리 영업시간은 이 데이터셋의 필수 항목이 아니다. 여기서 비영
    종료하면 Overpass 미러 하나가 죽었다는 이유로 도시 전체가 빠진다.
    """
    query = build_query(city)
    key = cache_key({"as_of": as_of, "stage": "overpass", "query": query})
    city_id = str(city["city_id"])

    def fetch() -> Any:
        last: Exception | None = None
        for endpoint in ENDPOINTS:
            try:
                return client.post_json("overpass", endpoint, data={"data": query})
            except Exception as exc:  # 미러가 죽는 일은 흔하다 — 다음 미러로 넘어간다
                last = exc
        raise last if last else RuntimeError("overpass: 엔드포인트가 없다")

    payload = client.cached_json(cache, city_id, "overpass", key, fetch)
    return facts_of(payload, qids)


def derive_tips(fact: Mapping[str, str]) -> list[dict[str, str]]:
    """원천 값에서 **유도된** 팁만 만든다 (DSN-39 · AC-071 · AC-072).

    문장 템플릿은 고정이고 원천 값을 그대로 끼운다. `evidence` 는 네 값으로 닫혀 있는데
    이번 굽기가 생성하는 것은 `opening_hours`·`admission` 둘뿐이다 — `duration`·`access`
    는 근거를 줄 공개 원천을 찾지 못했으므로 **0건**이고, 그 사실은 `known_gaps` 에 적힌다.

    "꼭 방문해 보세요" 류의 빈자리 메우기 문구는 여기서 나올 수 없다. 원천이 없으면
    빈 목록이고, 화면은 "미제공"을 그린다.
    """
    url = fact.get("url", "")
    tips: list[dict[str, str]] = []
    hours = (fact.get("opening_hours") or "").strip()
    if hours:
        tips.append(
            {
                "text": f"영업시간 {hours} (OpenStreetMap 기준 · 방문 전 확인)",
                "evidence": "opening_hours",
                "source_url": url,
            }
        )
    fee = (fact.get("fee") or "").strip().lower()
    if fee == "no":
        tips.append({"text": "입장 무료 (OpenStreetMap 기준)", "evidence": "admission", "source_url": url})
    elif fee == "yes":
        tips.append(
            {
                "text": "입장료 있음(금액 정보 미제공) (OpenStreetMap 기준)",
                "evidence": "admission",
                "source_url": url,
            }
        )
    return tips
