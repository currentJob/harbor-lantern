"""도시 가이드 베이커 — 빌드 타임 CLI (DSN-47 · 설계서 §16.18).

    uv run python tools/bake_city_guides.py --as-of 2026-09-15 [--city paris] [--review] [--dry-run]

**이것은 런타임 코드가 아니다.** 도시 하나를 굽는 데 수십 요청과 수십 초가 걸린다. 요청을
받은 자리에서 할 수 있는 일이 아니므로 조사는 여기서 하고, 런타임은 `seed/city-guides/` 의
구운 파일만 읽는다(NFR-017).

**수확 사슬에 SPARQL 이 없다**(§16.4 v1.5). 1단계는 한국어·영어 위키백과 `geosearch` 의
합집합이고(v1.6), 순위는 3단계 `wbgetentities` 의 `sitelinks` 가 매긴다 — WDQS 를 걷어낸
근거와 영어를 합친 근거는 둘 다 `bakery/geosearch.py` 의 모듈 설명에 있다.

**영어는 발견 경로일 뿐이다.** 싣는 것은 `kowiki` 문서가 있는 항목뿐이고 제목·설명은 언제나
한국어 문서에서 온다 — "한국어 100%" 불변식은 1단계가 아니라 3단계가 지킨다.

**판단은 전부 `harbor_lantern.domain` 이 한다.** 이 파일에 있는 것은 순서·I/O·보고서다.
분류(`guide_taxonomy.classify`) · 중복 접기(`guide_harvest.fold_rows`) · 연결 검증
(`guide_link.verify_link`) · 선별과 등급(`guide_grade.select_spots`/`grade_city`) 은 전부
순수 함수이고 고정 입력으로 테스트된다 — 베이커 자신은 테스트에서 실행되지 않는다
(NFR-019 · AC-081).

**`--as-of` 는 필수다.** 시계를 읽으면 두 번 실행한 결과의 바이트가 달라진다(함정 F14).
`retrieved_at` 은 전부 이 인자에서 온다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

# 한국어 Windows 콘솔은 기본이 cp949 라 `--help` 조차 UnicodeEncodeError 로 죽는다
# (이 저장소의 개발 기기가 그 환경이다). 도구가 자기 도움말도 못 내면 쓸 수 없으므로
# 출력 스트림을 여기서 UTF-8 로 맞춘다 — 호출자가 `-X utf8` 을 기억할 필요가 없어야 한다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from bakery import geosearch, io, osm, wikidata, wikipedia  # noqa: E402  (sys.path 조정 뒤에 와야 한다)
from bakery.io import BakeError  # noqa: E402
from harbor_lantern.domain.guide_grade import EXPORT_MAX, grade_city, select_spots  # noqa: E402
from harbor_lantern.domain.guide_harvest import dedupe_by_name, fold_rows  # noqa: E402
from harbor_lantern.domain.guide_link import LinkInput, apply_verdict, verify_link  # noqa: E402
from harbor_lantern.domain.guide_taxonomy import MAX_PROMOTION_DEPTH, Taxonomy, classify  # noqa: E402
from harbor_lantern.domain.models import LatLng  # noqa: E402

REGISTRY_PATH = ROOT / "tools" / "city-registry.json"
DENYLIST_PATH = ROOT / "tools" / "city-denylist.json"
CLASSES_PATH = ROOT / "tools" / "wikidata-classes.json"
OUT_DIR = ROOT / "seed" / "city-guides"
CACHE_DIR = ROOT / ".local" / "bake-cache"

CITY_ID_RE = re.compile(r"^[a-z0-9-]+$")

# 저녁 슬롯 후보가 되는 분류(§16.14). `category` 에서 유도하며 일몰 계산은 하지 않는다.
EVENING_ROOTS = frozenset({"viewpoint", "tower", "observation"})

# 한국어 문서가 없어 버린 항목을 보고서에 **몇 개까지** 이름으로 남길지. 총수는 언제나
# `harvest.no_korean_count` 로 센다 — 자르는 것은 목록이지 숫자가 아니다.
NO_KOREAN_REPORT_MAX = 25


# ─────────────────────────────────────────────────────────────────────────
# 입력 읽기
# ─────────────────────────────────────────────────────────────────────────
def load_taxonomy(path: Path | None = None) -> tuple[Taxonomy, dict[str, list[str]], dict[str, Any]]:
    """`tools/wikidata-classes.json` 이 분류의 SSoT 다 — `DEFAULT_TAXONOMY` 를 쓰지 않는다.

    도메인 모듈의 기본표는 예시이고, 굽기가 읽는 것은 커밋된 이 파일이다. 둘이 갈리면
    "고친 규칙이 왜 안 먹지"가 된다.
    """
    doc = io.read_json(path or CLASSES_PATH)
    taxonomy = Taxonomy(
        exclude_flat=frozenset(doc.get("exclude_flat", {})),
        allow_flat=dict(doc.get("allow_flat", {})),
        allow_root=dict(doc.get("allow_root", {})),
    )
    ancestry = {str(k): [str(v) for v in vs] for k, vs in dict(doc.get("ancestry_cache", {})).items()}
    return taxonomy, ancestry, doc


def load_denylist(path: Path | None = None) -> dict[str, list[dict[str, str]]]:
    """제외 목록을 읽는다. **`reason` 이 빈 항목이 있으면 그 자리에서 실패한다**(AC-084 자기검사 3).

    판정은 `bakery.io.validate_denylist`(순수 함수)가 하고 여기서는 읽고 멈출 뿐이다.
    네트워크를 타기 전에 검사한다 — 30분 굽고 나서 터지면 사람이 검사를 꺼 버린다.
    """
    cleaned, problems = io.validate_denylist(io.read_json(path or DENYLIST_PATH))
    if problems:
        raise BakeError(
            "city-denylist.json 에 근거 없는 제외가 있다 — 근거 없는 제외는 다음 사람이 "
            "되돌릴 수도 유지할 수도 없다:\n  " + "\n  ".join(problems)
        )
    return cleaned


def load_registry(path: Path | None = None) -> list[dict[str, Any]]:
    doc = io.read_json(path or REGISTRY_PATH)
    cities = [dict(city) for city in doc.get("cities", [])]
    for city in cities:
        city_id = str(city.get("city_id", ""))
        if not CITY_ID_RE.match(city_id):
            raise BakeError(f"city_id 가 [a-z0-9-]+ 가 아니다: {city_id!r}")
    cities.sort(key=lambda city: str(city["city_id"]))  # 도시는 city_id 오름차순 (DSN-40)
    return cities


# ─────────────────────────────────────────────────────────────────────────
# 승급 캐시 채우기 (§16.5 규칙 3)
# ─────────────────────────────────────────────────────────────────────────
def fill_ancestry(
    client: io.HttpClient,
    cache: io.ResponseCache,
    ancestry: dict[str, list[str]],
    direct_classes: Sequence[str],
    taxonomy: Taxonomy,
    as_of: str,
) -> dict[str, list[str]]:
    """평면 규칙으로 안 끝난 클래스들만 `P279` 로 최대 3단계 올린다.

    이미 `exclude_flat`·`allow_flat` 에서 끝나는 클래스는 조회하지 않는다 — 요청은
    도시가 늘수록 0 에 수렴해야 하고(§16.18 예산표), 그 수렴은 캐시와 이 가지치기에서 온다.
    """
    frontier = [
        qid
        for qid in io.sorted_unique(direct_classes)
        if qid not in taxonomy.exclude_flat and qid not in taxonomy.allow_flat
    ]
    for _ in range(MAX_PROMOTION_DEPTH):
        unknown = [qid for qid in frontier if qid not in ancestry]
        if unknown:
            ancestry.update(wikidata.fetch_ancestry(client, cache, unknown, as_of))
            for qid in unknown:
                ancestry.setdefault(qid, [])
        parents = io.sorted_unique([p for qid in frontier for p in ancestry.get(qid, [])])
        frontier = [qid for qid in parents if qid not in taxonomy.allow_root]
        if not frontier:
            break
    return ancestry


# ─────────────────────────────────────────────────────────────────────────
# 도시 하나 굽기
# ─────────────────────────────────────────────────────────────────────────
def harvest_city(
    client: io.HttpClient,
    cache: io.ResponseCache,
    city: Mapping[str, Any],
    taxonomy: Taxonomy,
    ancestry: dict[str, list[str]],
    denylist: Sequence[Mapping[str, str]],
    reviewed: bool,
    as_of: str,
    review: bool,
) -> dict[str, Any]:
    """1~6단계를 순서대로 돌려 그 도시의 스팟 목록과 지표를 만든다 (§16.4 사슬 표).

    반환값은 파일이 아니라 **중간 결과**다 — 쓰기는 호출자가 하고, `--dry-run` 은
    같은 계산을 하되 쓰지 않는다. 계산과 쓰기를 갈라 두면 자기검사(AC-084)를 쓰기
    전에 돌릴 수 있다.
    """
    city_id = str(city["city_id"])
    center = LatLng(float(city["center"]["lat"]), float(city["center"]["lng"]))
    # 반경은 `gsradius` 상한(10km)으로 잘릴 수 있다. **자른 반경으로 내려간다** — 대장의
    # 값으로 반경 불변식을 재면 긁지도 않은 바깥쪽을 통과시키게 된다(AC-065).
    effective_radius_m, radius_note = geosearch.clamp_radius(float(city["radius_m"]))
    radius_m = float(effective_radius_m)

    dropped_extra: list[dict[str, str]] = []

    # 1단계 — ko + en geosearch 의 **합집합** (도시당 2요청). **거리순이라 랭킹이 아니다.**
    # 여기서는 자르지 않는다 — 순위는 3단계 `sitelinks` 가 매긴다(§16.4).
    # 영어를 함께 긁는 이유는 `bakery/geosearch.py` 모듈 설명에 있다: 한국어 문서가 있어도
    # **그 문서에 좌표가 없으면** ko geosearch 가 돌려주지 않는다(프라하에서 카를교가 그랬다).
    pages: dict[str, list[dict[str, Any]]] = {
        lang: geosearch.fetch_pages(client, cache, city, as_of, lang) for lang in geosearch.LANGS
    }
    page_total = sum(len(rows) for rows in pages.values())
    if not page_total:
        return {
            "city_id": city_id,
            "status": "no_candidates",
            "reason": "1단계 geosearch 결과가 ko·en 모두 0건이다",
        }

    # 2단계 — pageid → Q-id (50개씩). **위키별로 자기 위키에 묻는다** — pageid 공간이 따로라
    # en 의 번호를 ko 에 물으면 오류가 아니라 다른 문서가 돌아온다.
    #
    # 합집합은 **Q-id 로** 짓는다. 같은 장소가 두 위키에 다른 제목으로 있어도 Q-id 는 하나다.
    mappings = {
        lang: geosearch.fetch_wikibase_items(
            client, cache, city_id, [page["pageid"] for page in pages[lang]], as_of, lang
        )
        for lang in geosearch.LANGS
    }
    # 합치는 것은 순수 함수가 한다 — 순서(Q-id 오름차순)와 동률 규칙이 거기 적혀 있고,
    # 고정 입력으로 검증된다(AC-081 은 베이커 자신의 실행을 금한다).
    union = geosearch.union_by_qid(pages, mappings)
    qid_pages = union.by_qid
    mapped_rows = union.mapped_rows
    for lang, page in union.unmapped:
        # 위키데이터 항목이 없는 문서는 3단계로 갈 수 없다. **조용히 빠지지 않는다.**
        dropped_extra.append(
            {
                "qid": "",
                "reason": "no_wikidata_item",
                "detail": f"[{lang}] {page['title']} (pageid {page['pageid']})",
            }
        )
    qids = list(qid_pages)
    if not qids:
        return {
            "city_id": city_id,
            "status": "no_candidates",
            "reason": f"geosearch 문서 {page_total}건 중 위키데이터 항목에 매핑된 것이 0건이다",
        }

    # 3단계 — sitelinks(랭킹) · P31(분류) · P625(좌표 대조) · 라벨. 50개씩 한 번에 온다.
    entities = wikidata.fetch_entities(client, cache, city_id, qids, str(city.get("native_lang", "")), as_of)

    # 4단계 — 네트워크 없음. 여기부터는 전부 도메인 순수 함수의 입력이다.
    rows: list[dict[str, Any]] = []
    coord_sources: dict[str, dict[tuple[float, float], str]] = {}
    ko_titles: dict[str, str] = {}
    no_korean_count = 0
    no_korean_rows: list[tuple[int, str, str]] = []  # (sitelinks, qid, en 제목)
    for qid in qids:
        entity = entities.get(qid)
        if entity is None:
            dropped_extra.append(
                {"qid": qid, "reason": "entity_missing", "detail": f"위키데이터가 항목을 돌려주지 않았다 ({qid})"}
            )
            continue
        # **한국어 문서가 없으면 싣지 않는다.** en 은 발견 경로일 뿐이고, 제목도 설명도
        # 한국어 문서에서 온다 — 한국어 문서가 없으면 영어 이름 + 빈 설명이 된다.
        # ko geosearch 로 온 항목은 그 자체가 한국어 문서이므로 sitelink 가 낡아 비어 있어도
        # 떨어뜨리지 않는다(예전 동작 그대로). en 으로만 온 항목은 `kowiki` 가 판정한다.
        ko_title = geosearch.korean_title(qid_pages[qid], wikidata.sitelink_title(entity, "kowiki"))
        if not ko_title:
            no_korean_count += 1
            en_title = str((qid_pages[qid].get("en") or {}).get("title", ""))
            no_korean_rows.append((wikidata.sitelink_count(entity), qid, en_title))
            continue
        ko_titles[qid] = ko_title

        sitelinks = wikidata.sitelink_count(entity)
        # 좌표는 출처가 셋이다: `P625` · ko 문서 좌표 · en 문서 좌표. **어느 것을 썼는지
        # 기록한다** — 연결 검증이 자기 자신과 대조하는 것을 막는 근거가 이 값이다.
        sources: dict[tuple[float, float], str] = {}
        for lat, lng in wikidata.claim_coords(entity):
            sources.setdefault((lat, lng), "wikidata:P625")
        if not sources:
            # `P625` 가 없는 항목은 문서 좌표(geosearch)를 쓴다. 문서 좌표는 반경 안이
            # 보장돼 있으므로 이것은 완화가 아니라 **같은 사실의 다른 출처**다.
            # ko 를 먼저 본다 — 같은 장소라도 언어판마다 대표점이 조금씩 다르고, 우리가
            # 싣는 문서는 한국어 쪽이다.
            for lang in geosearch.LANGS:
                page = qid_pages[qid].get(lang)
                if page:
                    sources[(page["lat"], page["lng"])] = f"geosearch:{lang}"
                    break
        coord_sources[qid] = sources
        rows.extend({"qid": qid, "lat": lat, "lng": lng, "sitelinks": sitelinks} for lat, lng in sources)

    # 한국어 문서가 없어 버린 것은 **수가 많다**(프라하 410건 · 실측 2026-09-16). 전부
    # 적으면 보고서가 그것만으로 차서 아무도 읽지 않는다 — 총수는 `no_korean_count` 로 세고,
    # 목록에는 **언어판이 많은 순으로 앞쪽만** 남긴다. 다음 사람이 "그렇게 유명한데 한국어
    # 문서가 없나"를 확인할 수 있는 만큼이면 된다. 순서는 결정론적이다.
    for sitelinks, qid, en_title in sorted(no_korean_rows, key=lambda row: (-row[0], row[1]))[:NO_KOREAN_REPORT_MAX]:
        dropped_extra.append(
            {
                "qid": qid,
                "reason": "no_korean_article",
                "detail": f"{en_title or qid} — kowiki 문서가 없다 (en 으로만 발견 · sitelinks {sitelinks})",
            }
        )

    if not ko_titles:
        return {
            "city_id": city_id,
            "status": "no_candidates",
            "reason": f"위키데이터 항목 {len(qids)}건 중 한국어 문서가 있는 것이 0건이다",
        }

    fold = fold_rows(rows, center, radius_m)

    # `sitelink_min` 은 이제 **질의 필터가 아니라 랭킹 하한**이다(§16.4 v1.5). 1단계에
    # 필터가 없으므로 여기서만 적용된다 — 대장에서 지우지 않는 이유는 "왜 이 도시에
    # 잡동사니가 실렸나"를 조절할 손잡이가 그것뿐이기 때문이다.
    sitelink_min = int(city["sitelink_min"])
    limit = int(city["limit"])
    ranked: list[Any] = []
    for candidate in fold.candidates:
        if candidate.sitelinks < sitelink_min:
            dropped_extra.append(
                {
                    "qid": candidate.qid,
                    "reason": "below_sitelink_min",
                    "detail": f"sitelinks {candidate.sitelinks} < 하한 {sitelink_min}",
                }
            )
            continue
        ranked.append(candidate)
    for candidate in ranked[limit:]:
        dropped_extra.append(
            {
                "qid": candidate.qid,
                "reason": "over_limit",
                "detail": f"순위 {limit} 밖 (sitelinks {candidate.sitelinks})",
            }
        )
    candidates = ranked[:limit]
    if not candidates:
        return {
            "city_id": city_id,
            "status": "no_candidates",
            "reason": f"후보 {len(fold.candidates)}건이 전부 랭킹 하한({sitelink_min}) 아래다",
        }

    qids = [c.qid for c in candidates]
    direct_map = {qid: wikidata.claim_qids(entities.get(qid, {}), "P31") for qid in qids}
    ancestry = fill_ancestry(
        client, cache, ancestry, [c for classes in direct_map.values() for c in classes], taxonomy, as_of
    )

    accepted: list[dict[str, Any]] = []
    excluded_count = 0
    unclassified: list[dict[str, Any]] = []
    composition: dict[str, int] = {}
    for candidate in candidates:
        entity = entities.get(candidate.qid, {})
        verdict = classify(direct_map.get(candidate.qid, []), ancestry, taxonomy)
        if verdict.decision == "exclude":
            excluded_count += 1
            continue
        name, name_source = wikidata.pick_name(entity, str(city.get("native_lang", "")))
        if verdict.decision == "unclassified":
            # 버리되 보고서에 남긴다 — 다음 굽기에서 사람이 허용목록에 올릴 수 있게(§16.5 규칙 4).
            unclassified.append(
                {"qid": candidate.qid, "name": name, "classes": direct_map.get(candidate.qid, [])}
            )
            continue
        root = verdict.root or "attraction"
        accepted.append(
            {
                "id": f"wd:{candidate.qid}",
                "wikidata_id": candidate.qid,
                "name": name,
                "name_source": name_source,
                "name_original": wikidata.pick_original(entity, str(city.get("native_lang", ""))),
                "lat": io.round_coord(candidate.coord.lat),
                "lng": io.round_coord(candidate.coord.lng),
                "category": root,
                "wikidata_classes": direct_map.get(candidate.qid, []),
                "importance": {"sitelinks": candidate.sitelinks},
                "area": "",
                "description": "",
                "description_source": None,
                "hours_text": "",
                "hours_source": None,
                "evening_candidate": root in EVENING_ROOTS,
                "tips": [],
                "recommendations": [],
                # 이 좌표가 어디서 왔는지. `wikidata:P625` · `geosearch:ko` · `geosearch:en`.
                # 기록하지 않으면 연결 검증이 **자기 자신과 대조**해 놓고 통과로 남는다.
                "coord_source": coord_sources.get(candidate.qid, {}).get(
                    (candidate.coord.lat, candidate.coord.lng), ""
                ),
                # 어느 경로로 발견됐는지: ko · en · both. 합집합이 값을 하는지 세는 근거다.
                "discovery": geosearch.discovery_of(qid_pages[candidate.qid]),
                "_area_qid": _first(wikidata.claim_qids(entity, "P131")),
                # 한국어 문서 제목. ko 로 발견한 항목은 **1단계가 이미 들고 있고**(우리가 그
                # 문서에서 왔다), en 으로만 발견한 항목은 `kowiki` sitelink 가 준다.
                "_ko_title": ko_titles[candidate.qid],
                "_en_label": wikidata.sitelink_title(entity, "enwiki"),
                "_entity_coord": wikidata.claim_coord(entity),
                # 문서 좌표(geosearch) 둘. `P625` 까지 **셋 다** 연결 검증에 쓴다 — 출처가
                # 셋이면 겨울 궁전 사건(다른 항목의 설명이 붙는 것)의 방어선이 세 겹이다.
                "_page_coords": {
                    lang: (page["lat"], page["lng"]) for lang, page in sorted(qid_pages[candidate.qid].items())
                },
            }
        )

    deduped = dedupe_by_name(accepted)
    spots = [dict(spot) for spot in deduped.kept]

    # 사람의 검토 — 수확과 선별 **사이**에 선다. 제외분이 상위 25를 차지하고 있으면
    # 등급까지 왜곡되기 때문이다(§16.9.3).
    deny_qids = {entry["qid"] for entry in denylist}
    kept = [spot for spot in spots if spot["wikidata_id"] not in deny_qids]
    denylisted_count = len(spots) - len(kept)
    harvested_qids = {spot["wikidata_id"] for spot in spots}
    unknown_deny = sorted(deny_qids - harvested_qids)

    candidate_ko = sum(1 for spot in spots if spot.get("name_source") == "ko")
    candidate_ko_ratio = io.round_ratio(candidate_ko / len(spots)) if spots else 0.0

    selected = [dict(spot) for spot in select_spots(kept, EXPORT_MAX)]

    # 합집합이 값을 하는지 **세어서** 남긴다. 다음 사람이 요청을 2.5배 쓸 값어치가 있었는지
    # 판단할 근거가 이 두 줄이다 — 수록된 스팟 기준과 한국어 문서가 있는 후보 풀 기준.
    discovery = _discovery_counts(spot.get("discovery", "") for spot in selected)
    discovery_pool = _discovery_counts(geosearch.discovery_of(qid_pages[qid]) for qid in ko_titles)

    if review:
        return {
            "city_id": city_id,
            "status": "review",
            "candidates": [dict(spot) for spot in kept],
            "candidate_count": len(candidates),
            "excluded_count": excluded_count,
            "unclassified": unclassified,
            "unknown_denylist_qids": unknown_deny,
            "discovery": discovery,
            "discovery_pool": discovery_pool,
            "no_korean_count": no_korean_count,
        }

    _fill_areas(client, cache, city_id, selected, as_of)
    described = _fill_descriptions(client, cache, city_id, selected, as_of)
    hours_count = _fill_hours(client, cache, city, selected, as_of)

    for spot in selected:
        for key in [key for key in spot if key.startswith("_")]:
            del spot[key]

    # 구성비는 **수록되는 스팟**에서 센다. 후보 전체에서 세면 제외·중복·선별로 빠진 것까지
    # 세어 "명소 밀도가 낮다"를 읽으려는 사람에게 없는 장소를 보여 준다(§16.5 의 다낭 사례).
    for spot in selected:
        root = str(spot["category"])
        composition[root] = composition.get(root, 0) + 1

    city_grade = grade_city(selected)
    # **검증 실패와 "한국어 문서가 없다"는 다른 사실이다.** 전자는 다른 장소의 설명이 붙을 뻔한
    # 것이고(겨울 궁전), 후자는 그냥 없는 것이다. 한 숫자로 합치면 위험한 쪽이 안 보인다.
    verification_failed = sum(
        1
        for spot in selected
        if spot["verification"]["status"] == "failed" and spot["verification"]["reason"] != "no_description"
    )

    return {
        "city_id": city_id,
        "status": "graded",
        "grade": city_grade.grade,
        "spots": selected,
        "reviewed": reviewed,
        "unknown_denylist_qids": unknown_deny,
        "unclassified": unclassified,
        "dropped": sorted(
            [
                {"qid": row.qid, "reason": row.reason, "detail": row.detail}
                for row in (*fold.dropped, *deduped.dropped)
            ]
            + dropped_extra,
            key=lambda row: (row["reason"], row["qid"], row["detail"]),
        ),
        "harvest": {
            "page_count": page_total,
            "ko_page_count": len(pages["ko"]),
            "en_page_count": len(pages["en"]),
            "mapped_count": len(qid_pages),
            "unmapped_count": page_total - mapped_rows,
            "korean_count": len(ko_titles),
            "no_korean_count": no_korean_count,
            "discovery": discovery,
            "discovery_pool": discovery_pool,
            "candidate_count": len(candidates),
            "effective_radius_m": effective_radius_m,
            "radius_note": radius_note,
            "excluded_count": excluded_count,
            "unclassified_count": len(unclassified),
            "denylisted_count": denylisted_count,
            "reviewed": reviewed,
            "spot_count": city_grade.spot_count,
            "grade_window": city_grade.grade_window,
            "ko_label_count": city_grade.ko_label_count,
            "ko_label_ratio": city_grade.ko_label_ratio,
            "candidate_ko_ratio": candidate_ko_ratio,
            "described_count": described,
            "verification_failed_count": verification_failed,
            "hours_count": hours_count,
            "composition": composition,
        },
    }


def _first(values: Sequence[str]) -> str:
    return values[0] if values else ""


def _discovery_counts(values: Any) -> dict[str, int]:
    """발견 경로 집계. **키는 항상 셋 다 있다** — 0 이 빠지면 '측정 안 함'과 구분되지 않는다."""
    counts = {"ko": 0, "en": 0, "both": 0}
    for value in values:
        key = str(value)
        if key in counts:
            counts[key] += 1
    return counts


def _fill_areas(
    client: io.HttpClient, cache: io.ResponseCache, city_id: str, spots: Sequence[dict[str, Any]], as_of: str
) -> None:
    """`P131` QID → 한국어 라벨. 없으면 영어, 그것도 없으면 `""` — **지어내지 않는다**(§16.13)."""
    area_qids = io.sorted_unique([spot["_area_qid"] for spot in spots if spot.get("_area_qid")])
    if not area_qids:
        return
    entities = wikidata.fetch_labels(client, cache, city_id, area_qids, as_of)
    for spot in spots:
        entity = entities.get(spot.get("_area_qid", ""), {})
        name, _source = wikidata.pick_name(entity, "")
        spot["area"] = name


def _fill_descriptions(
    client: io.HttpClient, cache: io.ResponseCache, city_id: str, spots: Sequence[dict[str, Any]], as_of: str
) -> int:
    """설명을 붙이고 **연결을 검증한다**(DSN-37).

    검증에 실패하면 설명만 비우고 장소는 남긴다(AC-088). 목록에서 빼면 틀린 것을 고친
    것이 아니라 숨긴 것이다. 설명을 시도한 모든 스팟이 `verification` 블록을 갖는다(AC-087) —
    한국어 문서가 아예 없는 경우도 `no_description` 으로 기록된다.
    """
    titles = [spot["_ko_title"] for spot in spots if spot.get("_ko_title")]
    pages = wikipedia.fetch_extracts(client, cache, city_id, titles, as_of) if titles else {}

    described = 0
    for index, spot in enumerate(spots):
        page = pages.get(spot.get("_ko_title", ""))
        names = [spot["name"], spot.get("name_original", ""), spot.get("_en_label", "")]
        page_title = page.title if page else spot.get("_ko_title", "")
        extract = page.extract if page else ""
        coord = page.coord if page else None
        if coord is None:
            # 문서에 좌표가 없으면 **스팟 좌표와 다른 출처의 좌표**로 대조한다(§16.4 v1.6).
            # 자기 자신과 재는 것은 대조가 아니다 — 그 규칙은 `independent_coord` 에 있다.
            coord = geosearch.independent_coord(
                (float(spot["lat"]), float(spot["lng"])),
                spot.get("_page_coords") or {},
                spot.get("_entity_coord"),
            )
        verdict = verify_link(
            LinkInput(
                spot_names=[name for name in names if name],
                spot_coord=LatLng(float(spot["lat"]), float(spot["lng"])),
                page_title=page_title,
                page_coord=None if coord is None else LatLng(float(coord[0]), float(coord[1])),
                has_description=bool(extract),
                redirected=bool(page.redirected) if page else False,
            )
        )
        merged = dict(spot)
        if extract:
            merged["description"] = extract
            merged["description_source"] = {
                "url": wikipedia.page_url(page_title),
                "title": page_title,
                "license": wikipedia.LICENSE,
                "license_url": wikipedia.LICENSE_URL,
                "retrieved_at": as_of,
            }
        applied = apply_verdict(merged, verdict)
        spots[index].clear()
        spots[index].update(applied)
        if applied.get("description"):
            described += 1
    return described


def _fill_hours(
    client: io.HttpClient, cache: io.ResponseCache, city: Mapping[str, Any], spots: Sequence[dict[str, Any]], as_of: str
) -> int:
    """영업시간·입장료를 QID 로 맞춰 붙이고, 원천이 있는 것만 팁으로 유도한다(DSN-39)."""
    qids = [spot["wikidata_id"] for spot in spots]
    try:
        facts = osm.fetch_facts(client, cache, city, qids, as_of)
    except Exception as exc:  # Overpass 실패는 도시를 떨어뜨리지 않는다
        print(f"  ! overpass 실패 — 영업시간 없이 진행한다: {exc}")
        return 0
    count = 0
    for spot in spots:
        fact = facts.get(spot["wikidata_id"])
        if not fact:
            continue
        spot["tips"] = osm.derive_tips(fact)
        hours = fact.get("opening_hours", "")
        if hours:
            spot["hours_text"] = hours
            spot["hours_source"] = {
                "url": fact.get("url", ""),
                "provider": osm.PROVIDER,
                "license": osm.LICENSE,
                "retrieved_at": as_of,
            }
            count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────
# 파일 만들기
# ─────────────────────────────────────────────────────────────────────────
def city_document(city: Mapping[str, Any], result: Mapping[str, Any], as_of: str) -> dict[str, Any]:
    harvest = dict(result["harvest"])
    spots = list(result["spots"])
    gaps = _known_gaps(harvest, spots)
    return {
        "schema_version": 1,
        "city_id": str(city["city_id"]),
        "name_ko": city["name_ko"],
        "name_local": city["name_local"],
        "name_en": city["name_en"],
        "country_code": city["country_code"],
        "country_ko": city["country_ko"],
        "country_en": city["country_en"],
        "center": {"lat": io.round_coord(city["center"]["lat"]), "lng": io.round_coord(city["center"]["lng"])},
        # **실제로 긁은 반경**이다. 대장의 값이 `gsradius` 상한을 넘으면 잘리고, 잘린 사실은
        # query.radius_clamped 와 known_gaps 에 남는다 — 조용히 자르지 않는다.
        "radius_m": int(harvest["effective_radius_m"]),
        "retrieved_at": as_of,
        "grade": result["grade"],
        "query": {
            # 하한은 이제 질의 필터가 아니라 **랭킹 하한**이다(§16.4 v1.5).
            "sitelink_min": int(city["sitelink_min"]),
            "limit": int(city["limit"]),
            "radius_m": int(harvest["effective_radius_m"]),
            "registry_radius_m": int(city["radius_m"]),
            "radius_clamped": int(harvest["effective_radius_m"]) != int(city["radius_m"]),
            "gslimit": geosearch.GSLIMIT_MAX,
            # 발견은 두 위키의 합집합이다. 수록은 여전히 한국어 문서가 있는 것만이다.
            "wikis": list(geosearch.LANGS),
            "page_count": int(harvest["page_count"]),
            "ko_page_count": int(harvest["ko_page_count"]),
            "en_page_count": int(harvest["en_page_count"]),
            "mapped_count": int(harvest["mapped_count"]),
            "korean_count": int(harvest["korean_count"]),
        },
        "harvest": harvest,
        "sources": _sources(as_of),
        "known_gaps": gaps,
        "spots": spots,
    }


def _sources(as_of: str) -> list[dict[str, str]]:
    return [
        {
            "what": "스팟 후보(중심 반경 안의 한국어 문서)와 문서 좌표 — 한국어 위키백과 지리 검색",
            "url": geosearch.KO_ENDPOINT,
            "retrieved_at": as_of,
        },
        {
            "what": (
                "스팟 후보 발견 경로 2 — 영어 위키백과 지리 검색. 한국어 문서에 좌표가 없어 "
                "한국어 검색에 안 잡히는 장소(카를교·프라하 천문시계 같은)를 찾는 데만 쓰고, "
                "수록은 한국어 문서가 있는 항목만 한다 — 제목·설명은 언제나 한국어 위키백과에서 온다"
            ),
            "url": geosearch.EN_ENDPOINT,
            "retrieved_at": as_of,
        },
        {
            "what": "이름·중요도(언어판 수)·분류(P31)·행정구역(P131)·좌표(P625)·문서 제목 — 위키데이터",
            "url": wikidata.ENDPOINT,
            "retrieved_at": as_of,
        },
        {
            "what": f"설명 — 한국어 위키백과 도입부 요약 ({wikipedia.LICENSE})",
            "url": wikipedia.ENDPOINT,
            "retrieved_at": as_of,
        },
        {
            "what": f"영업시간·입장료 — OpenStreetMap ({osm.LICENSE}) · QID 태그로 매칭",
            "url": osm.ENDPOINTS[0],
            "retrieved_at": as_of,
        },
    ]


def _known_gaps(harvest: Mapping[str, Any], spots: Sequence[Mapping[str, Any]]) -> list[str]:
    """빠진 것을 세어 적는다. **숨기면 사용자가 "이 앱이 다 아는구나"로 잘못 읽는다.**"""
    gaps: list[str] = []
    if harvest.get("radius_note"):
        # 반경이 잘렸다는 사실은 이 도시의 후보 수를 읽는 방법을 바꾼다. 적지 않으면
        # "여긴 원래 볼 게 없나 보다"로 읽힌다.
        gaps.append(str(harvest["radius_note"]))
    no_description = sum(1 for spot in spots if not spot.get("description"))
    if no_description:
        gaps.append(
            f"설명이 없는 스팟 {no_description}곳 — 한국어 위키백과 문서가 없거나 연결 검증에 "
            "실패했다. 영어 원문으로 대체하지 않았다."
        )
    if harvest.get("verification_failed_count"):
        gaps.append(
            f"그중 {harvest['verification_failed_count']}곳은 한국어 문서가 없어서가 아니라 **연결 검증에 "
            "실패해서** 설명을 비웠다 — 좌표나 제목이 맞지 않는 문서였다. 장소는 목록에 그대로 남아 있다."
        )
    no_hours = len(spots) - int(harvest.get("hours_count", 0))
    if no_hours > 0:
        gaps.append(
            f"영업시간이 없는 스팟 {no_hours}곳 — OpenStreetMap 에 해당 QID 요소가 없거나 "
            "opening_hours 태그가 없다."
        )
    if harvest.get("denylisted_count"):
        gaps.append(
            f"사람의 검토에서 제외한 항목 {harvest['denylisted_count']}건 — 사유는 "
            "tools/city-denylist.json 에 적혀 있다."
        )
    if not harvest.get("reviewed"):
        gaps.append("사람의 검토를 거치지 않은 도시다 — 자동 수확 결과를 그대로 실었다.")
    if harvest.get("unclassified_count"):
        gaps.append(
            f"분류에 걸리지 않아 제외한 후보 {harvest['unclassified_count']}건 — 버리되 굽기 "
            "보고서에 남겼다(다음 굽기에서 허용목록에 올릴 수 있게)."
        )
    unmapped = int(harvest.get("unmapped_count", 0))
    if unmapped > 0:
        gaps.append(
            f"반경 안의 문서 {unmapped}건은 위키데이터 항목이 없어 후보가 되지 못했다 — "
            "중요도와 분류를 매길 근거가 없다."
        )
    if harvest.get("no_korean_count"):
        gaps.append(
            f"영어 위키백과에서만 발견된 {harvest['no_korean_count']}건은 한국어 문서가 없어 "
            "싣지 않았다 — 영어 원문으로 대체하지 않았다. 굽기 보고서에는 그중 언어판이 많은 "
            f"{NO_KOREAN_REPORT_MAX}건까지 이름으로 남는다."
        )
    discovery = harvest.get("discovery") or {}
    gaps.append(
        "후보는 한국어·영어 위키백과 지리 검색의 **합집합**에서 오되, **한국어 문서가 있는 "
        f"것만** 싣는다(이 도시: 한국어 검색 {discovery.get('ko', 0)}곳 · 영어 검색에서만 "
        f"{discovery.get('en', 0)}곳 · 양쪽 {discovery.get('both', 0)}곳). 영어를 함께 긁는 "
        "이유는 한국어 문서가 있어도 그 문서에 좌표가 없으면 한국어 검색이 돌려주지 않기 "
        "때문이다. 그래도 **한국어 문서 자체가 없는 장소는 여전히 잡히지 않는다** — 이 앱은 "
        "한국어 가이드이고 설명도 한국어 위키백과에서 오므로 그런 장소는 영어 이름 + 빈 설명이 "
        "됐을 것이다. '이 도시에 이것밖에 없나'로 읽히지 않도록 여기 적는다."
    )
    gaps.append("소요시간·접근 교통 팁은 0건이다 — 근거를 줄 공개 원천을 이번 범위에서 찾지 못했다.")
    gaps.append(
        "설명은 위키백과 도입부 요약이라 '그 장소가 무엇인지'를 말한다. 사람이 쓴 홍콩 가이드처럼 "
        "'왜 가는지'를 말하지는 않는다."
    )
    return gaps


def index_document(exported: Sequence[Mapping[str, Any]], report: Mapping[str, Any], as_of: str) -> dict[str, Any]:
    # 등급별로 세고, 등급이 없는 것(질의 실패·후보 0건·대장에서 끈 도시)은 상태별로 센다.
    # 조사한 것과 수록한 것이 다르다는 사실이 여기서 바로 읽혀야 한다 — 그 차이가 REQ-029 다.
    counts: dict[str, int] = {"surveyed": int(report["surveyed"]), "exported": len(exported)}
    for grade in ("full", "partial", "below"):
        counts[grade] = 0
    for row in report["cities"]:
        grade = row.get("grade")
        if grade:
            counts[str(grade)] += 1
        else:
            counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    return {
        "schema_version": 1,
        "dataset": "city-guides",
        "retrieved_at": as_of,
        "what_this_is": (
            "한국어 위키백과·위키데이터·OpenStreetMap 에서 조사해 구운 도시 가이드다. 사람이 고르고 쓴 "
            "홍콩 가이드와 성격이 다르다 — 후보는 중심 반경 안의 한국어·영어 문서 합집합에서 오고(영어는 "
            "발견 경로일 뿐이며 한국어 문서가 있는 것만 싣는다), 순위는 언어판 수(sitelinks), 분류는 P31 "
            "이며, 설명은 한국어 위키백과 도입부 요약이다. 도시마다 등급(완전/부분)을 재어 함께 싣는다."
        ),
        "counts": counts,
        "sources": _sources(as_of),
        "known_gaps": [
            "구운 도시는 사람이 쓴 홍콩 가이드에 근접하지만 동일해지지 않는다 — 같은 방식으로 홍콩을 "
            "재현하면 상위 25건 중 한국어가 12건이고 주룽 모스크가 상위에 온다(실측 2026-09-15).",
            "등급이 미달인 도시는 수록하지 않는다. 조사는 했고 지표는 harvest-report.json 에 남는다.",
            "리조트 목적지는 이 방식으로 채워지지 않는다 — 나트랑 5건 · 푸꾸옥 3건(한국어 라벨 0)이고 "
            "반경 25km 로도 채워지지 않았다(실측 2026-09-15).",
            "조사 시점은 retrieved_at 이다. 자동 갱신은 하지 않는다 — 사람이 다시 굽는다.",
            "후보는 한국어·영어 위키백과 지리 검색의 합집합에서 오고, 그중 한국어 문서가 있는 것만 "
            "싣는다 — 한국어 문서가 없는 장소는 여전히 잡히지 않는다. 제약의 방향이 제품 목표(한국어 "
            "가이드)와 같지만, 도시별 스팟 수를 '그 도시의 전부'로 읽으면 안 된다. 어느 경로로 "
            "발견됐는지는 harvest-report.json 의 discovery 에 도시별로 남는다.",
            "지리 검색 반경 상한은 10km 다. 대장에 그보다 넓게 적힌 도시는 10km 로 잘렸고 그 사실은 "
            "각 도시 파일의 query.radius_clamped 와 known_gaps 에 적혀 있다.",
        ],
        "cities": [
            {
                "city_id": row["city_id"],
                "name_ko": row["name_ko"],
                "name_local": row["name_local"],
                "name_en": row["name_en"],
                "country_code": row["country_code"],
                "country_ko": row["country_ko"],
                "country_en": row["country_en"],
                "center": row["center"],
                "grade": row["grade"],
                "spot_count": row["harvest"]["spot_count"],
                "ko_label_ratio": row["harvest"]["ko_label_ratio"],
                "retrieved_at": row["retrieved_at"],
            }
            for row in exported
        ],
    }


# ─────────────────────────────────────────────────────────────────────────
# 자기검사 (AC-084) — 조용한 통과를 코드가 막는다
# ─────────────────────────────────────────────────────────────────────────
def self_check(
    index: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    report: Mapping[str, Any],
) -> list[str]:
    problems: list[str] = []

    indexed = sorted(city["city_id"] for city in index["cities"])
    reported = sorted(row["city_id"] for row in report["cities"] if row["status"] == "exported")
    if indexed != reported:
        problems.append(f"index.json 의 도시 집합 != 보고서의 exported 집합: {indexed} != {reported}")

    for doc in documents:
        recomputed = grade_city(doc["spots"])
        recorded = doc["harvest"]
        if (recomputed.grade, recomputed.spot_count, recomputed.ko_label_ratio) != (
            doc["grade"],
            recorded["spot_count"],
            recorded["ko_label_ratio"],
        ):
            problems.append(
                f"{doc['city_id']}: 기록된 등급/지표가 재계산과 다르다 — "
                f"기록 {doc['grade']}·{recorded['spot_count']}·{recorded['ko_label_ratio']} vs "
                f"재계산 {recomputed.grade}·{recomputed.spot_count}·{recomputed.ko_label_ratio}"
            )
        if doc["grade"] == "below":
            problems.append(f"{doc['city_id']}: 미달 도시가 수록 데이터셋에 있다")
        for spot in doc["spots"]:
            if spot.get("description"):
                verification = spot.get("verification")
                if not isinstance(verification, Mapping) or verification.get("status") != "passed":
                    problems.append(f"{doc['city_id']}/{spot['id']}: 설명이 있는데 검증 표시가 없다")
                source = spot.get("description_source") or {}
                if not all(source.get(field) for field in ("url", "license", "retrieved_at")):
                    problems.append(f"{doc['city_id']}/{spot['id']}: 설명이 있는데 출처 세 필드가 갖춰지지 않았다")
    return problems


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────
def print_review(city: Mapping[str, Any], result: Mapping[str, Any], top: int = 25) -> None:
    """상위 후보를 표로 찍는다. 사람은 이것을 읽고 `city-denylist.json` 을 채운다(DSN-50).

    자동이 못 하는 판단을 자동인 척하지 않는다 — 도쿄 1위가 전문학교 건물로 나온 것을
    분류 규칙으로 고치려 하면 로마의 콜로세움을 잃는 쪽으로 간다(실측 · 함정 F22).
    """
    print(f"\n=== {city['name_ko']} ({city['city_id']}) — 상위 {top}건 검토 ===")
    if result.get("radius_note"):
        print(f"  ! {result['radius_note']}")
    pool = result.get("discovery_pool", {})
    print(
        f"  발견 경로(한국어 문서가 있는 후보 풀): ko {pool.get('ko', 0)} · en 만 {pool.get('en', 0)} · "
        f"양쪽 {pool.get('both', 0)} · 한국어 문서가 없어 버린 en 항목 {result.get('no_korean_count', 0)}"
    )
    print(f"{'#':>3}  {'sitelinks':>9}  {'발견':<5} {'분류':<18} {'QID':<10} 이름")
    for rank, spot in enumerate(result["candidates"][:top], start=1):
        print(
            f"{rank:>3}  {spot['importance']['sitelinks']:>9}  {spot.get('discovery', ''):<5} "
            f"{spot['category']:<18} {spot['wikidata_id']:<10} {spot['name']}  "
            f"https://www.wikidata.org/wiki/{spot['wikidata_id']}"
        )
    if result["unclassified"]:
        print(f"\n  분류 미상 {len(result['unclassified'])}건 (버려졌다 — 허용목록에 올릴지 사람이 판단한다):")
        for row in result["unclassified"][:15]:
            print(f"    {row['qid']:<10} {row['name']}  P31={','.join(row['classes']) or '-'}")
    if result["unknown_denylist_qids"]:
        print(f"\n  ! 제외 목록에 있으나 수확 결과에 없는 QID: {', '.join(result['unknown_denylist_qids'])}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="도시 가이드 베이커 (DSN-47). 조사는 빌드 타임에, 런타임은 구운 파일만 읽는다.",
    )
    parser.add_argument(
        "--as-of",
        required=True,
        metavar="YYYY-MM-DD",
        help="조회일. 필수다 — 시계를 읽으면 두 실행의 바이트가 달라진다(AC-082 · 함정 F14)",
    )
    parser.add_argument("--city", action="append", default=[], help="도시 id. 반복 가능. 생략하면 대장 전체")
    parser.add_argument("--review", action="store_true", help="굽지 않고 상위 후보를 표로 출력한다(DSN-50)")
    parser.add_argument("--dry-run", action="store_true", help="보고서만 출력하고 seed/ 를 쓰지 않는다")
    parser.add_argument("--offline", action="store_true", help="캐시만 쓴다. 미적중이면 실패한다")
    args = parser.parse_args(argv)

    try:
        # 형식만 보면 2026-13-99 가 통과해 그대로 굽기가 시작된다(실제로 한 번 시작됐다).
        # 달력 날짜로 파싱한다 — 이것은 시계를 읽는 것이 아니라 인자를 검사하는 것이다.
        date.fromisoformat(args.as_of)
    except ValueError:
        parser.error("--as-of 는 실제 달력 날짜(YYYY-MM-DD)여야 한다")
    as_of = args.as_of

    try:
        taxonomy, ancestry, classes_doc = load_taxonomy()
        denylist = load_denylist()
        registry = load_registry()
    except BakeError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    wanted = list(args.city)
    for city_id in wanted:
        if city_id not in {str(city["city_id"]) for city in registry}:
            print(f"대장에 없는 도시다: {city_id}", file=sys.stderr)
            return 2

    if wanted:
        # 사람이 이름을 대면 enabled=false 인 도시도 조사한다 — 대장을 고치기 전에 다시 재 보는 경로다.
        target_ids = {str(city["city_id"]) for city in registry if str(city["city_id"]) in wanted}
    else:
        target_ids = {str(city["city_id"]) for city in registry if bool(city.get("enabled", True))}

    cache = io.ResponseCache(CACHE_DIR)
    report_rows: list[dict[str, Any]] = []
    exported: list[dict[str, Any]] = []

    with io.HttpClient(offline=args.offline) as client:
        for city in registry:
            city_id = str(city["city_id"])
            if city_id not in target_ids:
                if not wanted:
                    report_rows.append(
                        {
                            "city_id": city_id,
                            "status": "disabled",
                            "reason": str(city.get("disabled_reason", "대장에서 enabled=false")),
                        }
                    )
                continue

            print(f"[{city_id}] 수확 중 …")
            try:
                result = harvest_city(
                    client,
                    cache,
                    city,
                    taxonomy,
                    ancestry,
                    denylist.get(city_id, []),
                    city_id in denylist,
                    as_of,
                    args.review,
                )
            except BakeError as exc:
                print(f"  ! 실패: {exc}")
                report_rows.append({"city_id": city_id, "status": "query_failed", "reason": str(exc)})
                continue

            if args.review:
                print_review(city, result)
                continue

            if result["status"] != "graded":
                report_rows.append({"city_id": city_id, "status": result["status"], "reason": result.get("reason", "")})
                continue

            harvest = result["harvest"]
            row: dict[str, Any] = {
                "city_id": city_id,
                "status": "exported" if result["grade"] != "below" else "below_threshold",
                "grade": result["grade"],
                "reviewed": harvest["reviewed"],
                "spot_count": harvest["spot_count"],
                "ko_label_ratio": harvest["ko_label_ratio"],
                "grade_window": harvest["grade_window"],
                "candidate_count": harvest["candidate_count"],
                "candidate_ko_ratio": harvest["candidate_ko_ratio"],
                "page_count": harvest["page_count"],
                "ko_page_count": harvest["ko_page_count"],
                "en_page_count": harvest["en_page_count"],
                "mapped_count": harvest["mapped_count"],
                "korean_count": harvest["korean_count"],
                "no_korean_count": harvest["no_korean_count"],
                # 어느 경로로 발견됐는지 — 이 두 줄이 "합집합이 값을 했나"의 답이다.
                "discovery": harvest["discovery"],
                "discovery_pool": harvest["discovery_pool"],
                "effective_radius_m": harvest["effective_radius_m"],
                "denylisted": list(denylist.get(city_id, [])),
                "unknown_denylist_qids": result["unknown_denylist_qids"],
                "unclassified": result["unclassified"],
                "dropped": result["dropped"],
                "composition": harvest["composition"],
            }
            if result["grade"] == "below":
                row["reason"] = (
                    f"스팟 {harvest['spot_count']}개 · 한국어 라벨 비율 {harvest['ko_label_ratio']} — "
                    "등급 미달이라 수록하지 않는다"
                )
            if harvest["radius_note"]:
                # 조용히 자르지 않는다. 보고서에도 남지만, 굽는 사람이 그 자리에서 봐야 한다.
                row["radius_note"] = harvest["radius_note"]
                print(f"  ! {harvest['radius_note']}")
            if result["unknown_denylist_qids"]:
                # 오타가 조용히 아무것도 제외하지 않는 것을 막는다(§16.18). 굽기를 멈추지는
                # 않는다 — 대장을 고치는 것은 사람이고, 그 판단에 필요한 것은 사실뿐이다.
                print(f"  ! 제외 목록에 있으나 수확 결과에 없는 QID: {', '.join(result['unknown_denylist_qids'])}")
            report_rows.append(row)

            if result["grade"] != "below":
                exported.append(city_document(city, result, as_of))
            discovery = harvest["discovery"]
            print(
                f"  {result['grade']:<7} 스팟 {harvest['spot_count']:>3} · 한국어 "
                f"{harvest['ko_label_ratio']:.0%} · 설명 {harvest['described_count']} · 발견 "
                f"ko {discovery['ko']}/en {discovery['en']}/both {discovery['both']}"
            )

        # 보고서에 요청 수·캐시 적중을 싣지 않는다. 그 둘은 **이 실행**의 성질이지
        # 수확의 성질이 아니고, 캐시가 차 있느냐에 따라 값이 달라져 같은 입력에 다른
        # 바이트가 나온다(AC-082). 예산은 아래에서 stdout 으로 찍는다.
        budget = dict(sorted(client.requests.items()))
        cache_hits = cache.hits
        report = {
            "as_of": as_of,
            "surveyed": len([row for row in report_rows if row["status"] != "disabled"]),
            "cities": sorted(report_rows, key=lambda row: str(row["city_id"])),
        }

    if args.review:
        print("\n검토 모드다 — 아무것도 쓰지 않았다. tools/city-denylist.json 에 제외와 근거를 적고 다시 돌려라.")
        return 0

    graded = [row for row in report["cities"] if row.get("grade")]
    if report["surveyed"] and not graded:
        # 등급이 매겨진 도시가 **하나도** 없다면 그것은 데이터의 성질이 아니라 파이프라인의 고장이다.
        # 반대로 등급이 나왔는데 전부 미달인 것은 고장이 아니라 사실이므로 0 으로 끝낸다 —
        # 두 경우를 같은 종료 코드로 묶으면 T8 이 "부실한 도시들"과 "끊긴 배선"을 구분하지 못한다.
        print(
            f"\n조사한 {report['surveyed']}개 도시가 전부 질의 실패/후보 0건이다 — 배선을 의심하라.",
            file=sys.stderr,
        )
        return 1

    index = index_document(exported, report, as_of)
    problems = self_check(index, exported, report)
    if problems:
        print("\n자기검사 실패 — 아무것도 쓰지 않는다:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"\n--dry-run: 수록 {len(exported)} 도시 · 조사 {report['surveyed']} 도시 (파일은 쓰지 않았다)")
    else:
        written = io.write_json(OUT_DIR / "index.json", index)
        for document in exported:
            written += io.write_json(OUT_DIR / f"{document['city_id']}.json", document)
        written += io.write_json(OUT_DIR / "harvest-report.json", report)
        classes_doc["ancestry_cache"] = {qid: sorted(parents) for qid, parents in sorted(ancestry.items())}
        io.write_json(CLASSES_PATH, classes_doc)
        print(f"\n수록 {len(exported)} 도시 · {written:,} 바이트 · {OUT_DIR}")

    print(f"요청: {budget} (합계 {sum(budget.values())}) · 캐시 적중 {cache_hits}")
    surveyed = max(1, report["surveyed"])
    # 사슬은 도시당 geosearch 2(ko·en) + pageprops ceil(ko문서/50) + ceil(en문서/50)
    # + wbgetentities ceil(후보/50) + areas 1 + extracts ceil(스팟/20) + overpass 1 이다(§16.4).
    # en 이 상한(500)에 닿는 큰 도시는 pageprops-en 만 10요청이라 예산이 2~3배가 된다.
    print(f"도시당 평균 요청 {sum(budget.values()) / surveyed:.1f} (합집합 사슬 기준 약 20~35)")
    return 0


if __name__ == "__main__":  # pragma: no cover - 사람이 손으로 돌리는 진입점이다
    try:
        raise SystemExit(main())
    except BakeError as error:  # 예상한 실패는 역추적 없이 사유만 찍는다
        print(f"굽기 실패: {error}", file=sys.stderr)
        raise SystemExit(1) from None
