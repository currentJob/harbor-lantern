"""도시 가이드 베이커 — 빌드 타임 CLI (DSN-47 · 설계서 §16.18).

    uv run python tools/bake_city_guides.py --as-of 2026-09-15 [--city paris] [--review] [--dry-run]

**이것은 런타임 코드가 아니다.** 위키데이터 질의 서비스는 요청을 받은 자리에서 부를 수
없다 — 공개 엔드포인트 상한이 60초인데 파리가 76.0초에 504 를 냈다(실측 2026-09-15).
그래서 조사는 여기서 하고, 런타임은 `seed/city-guides/` 의 구운 파일만 읽는다(NFR-017).

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

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from bakery import io, osm, wdqs, wikidata, wikipedia  # noqa: E402  (sys.path 조정 뒤에 와야 한다)
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
    """1~4단계를 순서대로 돌려 그 도시의 스팟 목록과 지표를 만든다.

    반환값은 파일이 아니라 **중간 결과**다 — 쓰기는 호출자가 하고, `--dry-run` 은
    같은 계산을 하되 쓰지 않는다. 계산과 쓰기를 갈라 두면 자기검사(AC-084)를 쓰기
    전에 돌릴 수 있다.
    """
    city_id = str(city["city_id"])
    center = LatLng(float(city["center"]["lat"]), float(city["center"]["lng"]))
    radius_m = float(city["radius_m"])

    # 1단계 — 지리 + sitelinks (클래스 조인 없음)
    rows = wdqs.fetch_rows(client, cache, city, as_of)
    fold = fold_rows(rows, center, radius_m)
    candidates = fold.candidates
    if not candidates:
        return {"city_id": city_id, "status": "no_candidates", "reason": "1단계 후보가 0건이다"}

    # 2단계 — 라벨·문서 제목·클레임 (분류·행정구역·검증 좌표가 같은 응답에 실려 온다)
    qids = [c.qid for c in candidates]
    entities = wikidata.fetch_entities(client, cache, city_id, qids, str(city.get("native_lang", "")), as_of)

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
                "_area_qid": _first(wikidata.claim_qids(entity, "P131")),
                "_ko_title": wikidata.sitelink_title(entity, "kowiki"),
                "_en_label": wikidata.sitelink_title(entity, "enwiki"),
                "_entity_coord": wikidata.claim_coord(entity),
            }
        )

    deduped = dedupe_by_name(accepted)
    spots = [dict(spot) for spot in deduped.kept]

    # 3단계(사람의 검토) — 수확과 선별 **사이**에 선다. 제외분이 상위 25를 차지하고 있으면
    # 등급까지 왜곡되기 때문이다(§16.9.3).
    deny_qids = {entry["qid"] for entry in denylist}
    kept = [spot for spot in spots if spot["wikidata_id"] not in deny_qids]
    denylisted_count = len(spots) - len(kept)
    harvested_qids = {spot["wikidata_id"] for spot in spots}
    unknown_deny = sorted(deny_qids - harvested_qids)

    candidate_ko = sum(1 for spot in spots if spot.get("name_source") == "ko")
    candidate_ko_ratio = io.round_ratio(candidate_ko / len(spots)) if spots else 0.0

    selected = [dict(spot) for spot in select_spots(kept, EXPORT_MAX)]

    if review:
        return {
            "city_id": city_id,
            "status": "review",
            "candidates": [dict(spot) for spot in kept],
            "candidate_count": len(candidates),
            "excluded_count": excluded_count,
            "unclassified": unclassified,
            "unknown_denylist_qids": unknown_deny,
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
        "dropped": [
            {"qid": row.qid, "reason": row.reason, "detail": row.detail}
            for row in (*fold.dropped, *deduped.dropped)
        ],
        "harvest": {
            "candidate_count": len(candidates),
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
        if coord is None and spot.get("_entity_coord"):
            # 문서에 좌표가 없으면 위키데이터 항목 좌표로 대조한다. 이것은 **완화가 아니다** —
            # 항목 좌표와 스팟 좌표가 다른 경우(다른 항목의 설명이 붙은 경우)를 그대로 잡는다.
            coord = spot["_entity_coord"]
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
        "radius_m": int(city["radius_m"]),
        "retrieved_at": as_of,
        "grade": result["grade"],
        "query": {
            "sitelink_min": int(city["sitelink_min"]),
            "limit": int(city["limit"]),
            "radius_m": int(city["radius_m"]),
        },
        "harvest": harvest,
        "sources": _sources(as_of),
        "known_gaps": gaps,
        "spots": spots,
    }


def _sources(as_of: str) -> list[dict[str, str]]:
    return [
        {
            "what": "스팟 후보와 중요도(언어판 수) — 위키데이터 질의 서비스 지리 검색",
            "url": wdqs.ENDPOINT,
            "retrieved_at": as_of,
        },
        {
            "what": "이름·분류(P31)·행정구역(P131)·좌표(P625)·문서 제목 — 위키데이터",
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
            "위키데이터·위키백과·OpenStreetMap 에서 조사해 구운 도시 가이드다. 사람이 고르고 쓴 "
            "홍콩 가이드와 성격이 다르다 — 스팟은 언어판 수 순위와 분류로 뽑았고 설명은 위키백과 "
            "도입부 요약이다. 도시마다 등급(완전/부분)을 재어 함께 싣는다."
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
    print(f"{'#':>3}  {'sitelinks':>9}  {'분류':<18} {'QID':<10} 이름")
    for rank, spot in enumerate(result["candidates"][:top], start=1):
        print(
            f"{rank:>3}  {spot['importance']['sitelinks']:>9}  {spot['category']:<18} "
            f"{spot['wikidata_id']:<10} {spot['name']}  "
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
            if result["unknown_denylist_qids"]:
                # 오타가 조용히 아무것도 제외하지 않는 것을 막는다(§16.18). 굽기를 멈추지는
                # 않는다 — 대장을 고치는 것은 사람이고, 그 판단에 필요한 것은 사실뿐이다.
                print(f"  ! 제외 목록에 있으나 수확 결과에 없는 QID: {', '.join(result['unknown_denylist_qids'])}")
            report_rows.append(row)

            if result["grade"] != "below":
                exported.append(city_document(city, result, as_of))
            print(
                f"  {result['grade']:<7} 스팟 {harvest['spot_count']:>3} · 한국어 "
                f"{harvest['ko_label_ratio']:.0%} · 설명 {harvest['described_count']}"
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
    print(f"도시당 평균 요청 {sum(budget.values()) / surveyed:.1f} (예산표 기준 약 10)")
    return 0


if __name__ == "__main__":  # pragma: no cover - 사람이 손으로 돌리는 진입점이다
    try:
        raise SystemExit(main())
    except BakeError as error:  # 예상한 실패는 역추적 없이 사유만 찍는다
        print(f"굽기 실패: {error}", file=sys.stderr)
        raise SystemExit(1) from None
