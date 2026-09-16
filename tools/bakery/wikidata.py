"""2단계 — `wbgetentities` 배치 (DSN-36 · 설계서 §16.7 · 분류 입력은 §16.5).

한 번의 응답이 **네 가지**를 준다: 한국어·영어·현지어 라벨, kowiki/enwiki 문서 제목,
`P31`(분류), `P131`(행정구역), `P625`(연결 검증용 좌표). 같은 요청 하나가 두 가지
일을 하므로 **분류를 위해 요청이 늘지 않는다**(§16.4 의 마지막 근거).

배치 상한 50 은 공급자 고지다
(https://www.wikidata.org/w/api.php?action=help&modules=wbgetentities · 2026-09-15 조회).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bakery.io import WBGETENTITIES_BATCH, HttpClient, ResponseCache, cache_key, chunked

__all__ = [
    "ENDPOINT",
    "claim_coord",
    "claim_qids",
    "fetch_ancestry",
    "fetch_entities",
    "fetch_labels",
    "monolingual",
    "pick_name",
    "pick_original",
    "sitelink_title",
]

ENDPOINT = "https://www.wikidata.org/w/api.php"


def fetch_entities(
    client: HttpClient,
    cache: ResponseCache,
    city_id: str,
    qids: Sequence[str],
    native_lang: str,
    as_of: str,
) -> dict[str, dict[str, Any]]:
    """후보 QID 들의 라벨·문서 제목·클레임. `ceil(후보/50)` 요청 (§16.18 예산표)."""
    languages = "|".join(_unique(["ko", "en", native_lang]))
    return _fetch(
        client,
        cache,
        city_id,
        "wbgetentities",
        qids,
        as_of,
        props="labels|sitelinks|claims",
        languages=languages,
        sitefilter="kowiki|enwiki",
    )


def fetch_labels(
    client: HttpClient,
    cache: ResponseCache,
    city_id: str,
    qids: Sequence[str],
    as_of: str,
) -> dict[str, dict[str, Any]]:
    """행정구역(`P131`) 같은 참조 항목의 라벨만 받는다 — 클레임은 받지 않는다.

    `props` 를 줄이는 것이 응답 크기를 줄이고, 줄어든 응답이 캐시 파일 크기를 줄인다.
    지역명은 라벨 하나면 되는데 클레임까지 받으면 도시마다 수 MB 가 캐시에 쌓인다.
    """
    return _fetch(
        client,
        cache,
        city_id,
        "wbgetentities-areas",
        qids,
        as_of,
        props="labels",
        languages="ko|en",
        sitefilter=None,
    )


def fetch_ancestry(
    client: HttpClient,
    cache: ResponseCache,
    class_qids: Sequence[str],
    as_of: str,
) -> dict[str, list[str]]:
    """클래스들의 상위(`P279`). 승급 캐시를 채운다 (§16.5).

    도시별이 아니라 **공유** 캐시에 둔다(`_shared`) — 클래스는 도시에 속하지 않고,
    도시가 늘수록 적중률이 올라 요청이 0 에 수렴한다는 설계 전제가 이 공유에서 나온다.
    """
    entities = _fetch(
        client,
        cache,
        "_shared",
        "ancestry",
        class_qids,
        as_of,
        props="claims",
        languages=None,
        sitefilter=None,
    )
    return {qid: claim_qids(entity, "P279") for qid, entity in sorted(entities.items())}


def _fetch(
    client: HttpClient,
    cache: ResponseCache,
    city_id: str,
    stage: str,
    qids: Sequence[str],
    as_of: str,
    *,
    props: str,
    languages: str | None,
    sitefilter: str | None,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for batch in chunked(sorted(set(qids)), WBGETENTITIES_BATCH):
        params: dict[str, Any] = {
            "action": "wbgetentities",
            "format": "json",
            "formatversion": "2",
            "ids": "|".join(batch),
            "props": props,
        }
        if languages:
            params["languages"] = languages
            params["languagefallback"] = "0"
        if sitefilter:
            params["sitefilter"] = sitefilter
        key = cache_key({"as_of": as_of, "stage": stage, "params": params})
        payload = client.cached_json(
            cache,
            city_id,
            stage,
            key,
            lambda params=params: client.get_json(stage, ENDPOINT, params=params),
        )
        entities = payload.get("entities") if isinstance(payload, Mapping) else None
        if isinstance(entities, Mapping):
            for qid, entity in entities.items():
                if isinstance(entity, Mapping) and "missing" not in entity:
                    merged[str(qid)] = dict(entity)
    return merged


# ─────────────────────────────────────────────────────────────────────────
# 응답 읽기 — 전부 순수 함수다 (고정 응답으로 검증 가능)
# ─────────────────────────────────────────────────────────────────────────
def _label(entity: Mapping[str, Any], lang: str) -> str:
    labels = entity.get("labels")
    if not isinstance(labels, Mapping):
        return ""
    value = labels.get(lang)
    if isinstance(value, Mapping):
        return str(value.get("value") or "")
    return str(value or "")


def pick_name(entity: Mapping[str, Any], native_lang: str) -> tuple[str, str]:
    """한국어 이름과 **그 이름이 어디서 왔는지** (§16.7 · O15 확정).

    폴백 순서는 ko → en → 현지어 → 아무 라벨이고 **빈 문자열이 되지 않는다**(AC-069).
    두 번째 값(`name_source`)이 없으면 폴백으로 채운 이름을 한국어 라벨로 세게 되고,
    그러면 등급(AC-083)이 스스로를 증명한다 — 그래서 출처를 함께 돌려준다.
    """
    for lang, source in (("ko", "ko"), ("en", "en"), (native_lang, "native")):
        if lang:
            value = _label(entity, lang)
            if value:
                return value, source
    labels = entity.get("labels")
    if isinstance(labels, Mapping):
        for lang in sorted(labels):
            value = _label(entity, lang)
            if value:
                return value, "other"
    return "", "none"


def monolingual(entity: Mapping[str, Any], prop: str) -> str:
    """`P1705`(원어명) 같은 monolingualtext 클레임의 첫 값."""
    for claim in _claims(entity, prop):
        value = _datavalue(claim)
        if isinstance(value, Mapping):
            text = value.get("text")
            if text:
                return str(text)
    return ""


def pick_original(entity: Mapping[str, Any], native_lang: str) -> str:
    """원어명: `P1705` → 현지어 라벨 → en 라벨 → `""` (§16.7).

    없으면 빈 문자열이다. 화면은 한국어 이름만 보인다 — **지어내지 않는다.**
    """
    native_name = monolingual(entity, "P1705")
    if native_name:
        return native_name
    if native_lang:
        value = _label(entity, native_lang)
        if value:
            return value
    return _label(entity, "en")


def claim_qids(entity: Mapping[str, Any], prop: str) -> list[str]:
    """클레임의 항목 값(QID)들. **응답에 실린 순서를 지킨다**.

    `classify()` 가 같은 순위의 규칙 안에서 `direct_classes` 순서대로 보고, 어느 클래스가
    판정을 냈는지(`matched_class`)가 사후 감사의 단서다 — 여기서 정렬해 버리면 그 단서가
    응답과 어긋난다.
    """
    found: list[str] = []
    for claim in _claims(entity, prop):
        value = _datavalue(claim)
        if isinstance(value, Mapping):
            qid = value.get("id")
            if qid and str(qid) not in found:
                found.append(str(qid))
    return found


def claim_coord(entity: Mapping[str, Any]) -> tuple[float, float] | None:
    """`P625` 좌표. 없으면 `None` — **`(0, 0)` 으로 채우지 않는다**.

    둘을 같은 값으로 표현하면 대척점 거리 계산이 통과해 버린다(`guide_link.LinkInput`
    의 주석과 같은 이유).
    """
    for claim in _claims(entity, "P625"):
        value = _datavalue(claim)
        if isinstance(value, Mapping):
            lat, lng = value.get("latitude"), value.get("longitude")
            if lat is not None and lng is not None:
                return float(lat), float(lng)
    return None


def sitelink_title(entity: Mapping[str, Any], site: str) -> str:
    """`kowiki`·`enwiki` 문서 제목. 없으면 `""`."""
    sitelinks = entity.get("sitelinks")
    if not isinstance(sitelinks, Mapping):
        return ""
    link = sitelinks.get(site)
    if isinstance(link, Mapping):
        return str(link.get("title") or "")
    return ""


def _claims(entity: Mapping[str, Any], prop: str) -> list[Mapping[str, Any]]:
    claims = entity.get("claims")
    if not isinstance(claims, Mapping):
        return []
    statements = claims.get(prop)
    if not isinstance(statements, list):
        return []
    return [s for s in statements if isinstance(s, Mapping)]


def _datavalue(claim: Mapping[str, Any]) -> Any:
    mainsnak = claim.get("mainsnak")
    if not isinstance(mainsnak, Mapping):
        return None
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, Mapping):
        return None
    return datavalue.get("value")


def _unique(values: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen
