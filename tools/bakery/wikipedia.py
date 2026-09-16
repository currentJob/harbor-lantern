"""3단계 — 한국어 위키백과 `extracts` + `coordinates` 배치 (DSN-36 · DSN-37 · §16.7 · §16.8).

`exintro=1&explaintext=1&exsentences=3` 조합이 정찰 실측에서 **170~260자**의 한국어
요약을 줬다 — 사람이 쓴 홍콩 가이드의 `description` 길이와 거의 같다. 그래서 새 요약
규칙을 만들지 않는다.

`prop` 에 `coordinates` 를 **함께** 건다. 요청 수는 그대로인데 연결 검증(§16.8)의 근거가
공짜로 들어온다 — 검증을 위해 요청을 따로 더 하면 예산이 빠듯할 때 "이번엔 건너뛰자"가
된다(함정 F18).

배치 상한 20 과 "다건 요청에는 `exintro` 필수"는 공급자 고지다
(https://en.wikipedia.org/w/api.php?action=help&modules=query%2Bextracts · 2026-09-15 조회).
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bakery.io import EXTRACTS_BATCH, HttpClient, ResponseCache, cache_key, chunked

__all__ = ["ENDPOINT", "LICENSE", "LICENSE_URL", "PageExtract", "fetch_extracts", "page_url", "parse_payload"]

ENDPOINT = "https://ko.wikipedia.org/w/api.php"

# 본문 라이선스. 출처 링크로 저작자 표시 요건을 만족하는 것으로 본다
# (https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use · 2026-09-15 조회 · A12).
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


@dataclass(frozen=True)
class PageExtract:
    """문서 하나. `title` 은 `normalized`·`redirects` 를 따라간 **최종 제목**이다.

    리디렉트 자체는 흔하므로 실패로 보지 않되 있었다는 사실을 남긴다 — 제목이 조용히
    다른 주제로 옮겨 가는 경로가 여기다(§16.8 방어선 2).
    """

    requested: str
    title: str
    extract: str
    coord: tuple[float, float] | None
    redirected: bool
    missing: bool


def page_url(title: str) -> str:
    """출처 URL. 제목의 공백은 밑줄이고, 나머지는 퍼센트 인코딩이다."""
    return "https://ko.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe=":/_()%-")


def parse_payload(payload: Mapping[str, Any], requested: Sequence[str]) -> dict[str, PageExtract]:
    """응답 하나 → 요청 제목별 결과. **순수 함수다**(고정 응답으로 검증된다).

    `normalized`(표기 정규화)와 `redirects`(문서 이동)를 따라가 최종 제목을 찾는다.
    둘을 구분해 들고 있는 이유는 `redirected` 플래그가 검증 기록에 남기 때문이다.
    """
    query = payload.get("query") if isinstance(payload, Mapping) else None
    query = query if isinstance(query, Mapping) else {}
    normalized = _mapping(query.get("normalized"))
    redirects = _mapping(query.get("redirects"))
    pages = query.get("pages")
    by_title: dict[str, Mapping[str, Any]] = {}
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, Mapping):
                by_title[str(page.get("title") or "")] = page
    elif isinstance(pages, Mapping):  # formatversion=1 응답도 읽는다
        for page in pages.values():
            if isinstance(page, Mapping):
                by_title[str(page.get("title") or "")] = page

    results: dict[str, PageExtract] = {}
    for title in requested:
        final = title
        redirected = False
        for _ in range(5):  # 체인 상한. 루프가 없다는 것이 코드로 보여야 한다.
            if final in normalized:
                final = normalized[final]
                continue
            if final in redirects:
                final = redirects[final]
                redirected = True
                continue
            break
        page = by_title.get(final)
        if page is None:
            results[title] = PageExtract(title, final, "", None, redirected, True)
            continue
        missing = bool(page.get("missing"))
        extract = "" if missing else " ".join(str(page.get("extract") or "").split())
        results[title] = PageExtract(title, final, extract, _coord(page), redirected, missing)
    return results


def fetch_extracts(
    client: HttpClient,
    cache: ResponseCache,
    city_id: str,
    titles: Sequence[str],
    as_of: str,
) -> dict[str, PageExtract]:
    """`ceil(스팟/20)` 요청 (§16.18 예산표). 한국어 문서가 없으면 그 자리는 빈 설명이다.

    **영어 원문을 대신 싣지 않는다** — 한국어 앱에서 "설명이 있다"는 표시와 실제로
    읽히는 것이 어긋난다. 건수는 그 도시의 `known_gaps` 에 적힌다.
    """
    wanted = [title for title in titles if title]
    results: dict[str, PageExtract] = {}
    for batch in chunked(sorted(set(wanted)), EXTRACTS_BATCH):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "extracts|coordinates",
            "exintro": "1",
            "explaintext": "1",
            "exsentences": "3",
            "exlimit": str(EXTRACTS_BATCH),
            "redirects": "1",
            "titles": "|".join(batch),
        }
        key = cache_key({"as_of": as_of, "stage": "extracts", "params": params})
        payload = client.cached_json(
            cache,
            city_id,
            "extracts",
            key,
            lambda params=params: client.get_json("extracts", ENDPOINT, params=params),
        )
        results.update(parse_payload(payload, batch))
    return results


def _mapping(raw: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping) and item.get("from") and item.get("to"):
                found[str(item["from"])] = str(item["to"])
    return found


def _coord(page: Mapping[str, Any]) -> tuple[float, float] | None:
    coords = page.get("coordinates")
    if isinstance(coords, list):
        for coord in coords:
            if isinstance(coord, Mapping) and coord.get("lat") is not None and coord.get("lon") is not None:
                return float(coord["lat"]), float(coord["lon"])
    return None
