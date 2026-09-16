"""후보 정규화 — DSN-35 (설계서 §16.6 · REQ-022 · AC-065).

1단계 SPARQL(§16.4)은 **항목당 여러 행**을 돌려준다. `P625`(좌표) 문이 여러 개인 항목이
그대로 여러 행이 되기 때문이다 — 정찰에서 **도쿄 스미다강 2행 · 다낭 선짜산 3행**이 나왔다.
접는 일을 SPARQL 의 `SAMPLE()` 에 맡기지 않는 이유가 둘이다.

1. `SAMPLE()` 은 어느 값이 올지 정의되지 않는다. 같은 질의에 다른 바이트가 나오면
   NFR-020(재현성)이 깨진다.
2. **중심에 가장 가까운 좌표**를 고르면 반경 불변식이 공짜로 따라온다 — 채택한 좌표가
   반경을 벗어나면 그 항목의 다른 어떤 좌표도 벗어나므로, 접은 뒤의
   `haversine_m(center, coord) <= radius_m` 재검증이 그 항목을 정확히 떨어뜨린다(AC-065).

**좌표 중복은 접지 않는다.** 같은 건물에 든 서로 다른 명소가 사라진다(§6.23 에서 이미 배웠다).
접는 것은 **같은 QID 의 여러 좌표**와 **같은 표기의 다른 QID**(`dedupe_by_name`) 둘뿐이다.

이름 중복 제거가 별도 함수인 이유: 1단계는 **라벨을 받지 않는다**(§16.4 — `wikibase:label`
서비스가 ko→en 폴백을 조용히 해서 한국어 라벨 비율을 셀 수 없게 된다). 이름은 2단계
`wbgetentities` 응답에서 오므로, 이름 중복은 그것이 붙은 뒤에 판정한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng

__all__ = [
    "Candidate",
    "DroppedRow",
    "FoldResult",
    "NameDedupeResult",
    "dedupe_by_name",
    "fold_candidates",
    "fold_rows",
]


@dataclass(frozen=True)
class Candidate:
    """접힌 후보 하나. 좌표는 **반올림하지 않는다**(`places.Place` 와 같은 규칙).

    `coord_statements` 는 이 항목이 원래 몇 행이었는지다 — 스미다강 2 · 선짜산 3.
    굽기 보고서가 "몇 건이 접혔나"를 셀 수 있게 남긴다(조용히 줄지 않게).
    """

    qid: str
    coord: LatLng
    sitelinks: int
    distance_m: float
    coord_statements: int


@dataclass(frozen=True)
class DroppedRow:
    """떨어뜨린 것. **이유 없이 사라지는 행이 없어야 한다**(AC-065 의 집계 일치)."""

    qid: str
    reason: str  # 'out_of_radius' | 'malformed_row' | 'duplicate_name'
    detail: str = ""


@dataclass(frozen=True)
class FoldResult:
    candidates: tuple[Candidate, ...]
    dropped: tuple[DroppedRow, ...]


@dataclass(frozen=True)
class NameDedupeResult:
    kept: tuple[Mapping[str, Any], ...]
    dropped: tuple[DroppedRow, ...]


def fold_candidates(
    rows: Sequence[Mapping[str, Any]],
    center: LatLng,
    radius_m: float,
) -> tuple[Candidate, ...]:
    """1단계 행들을 항목 단위로 접고 반경을 재검증한다 (§16.6 계약 시그니처).

    정렬은 `(-sitelinks, qid)` **고정**이다. 이 순서가 그대로 중요도 순위이고
    AC-068(상위 스팟 포함)의 기준이며, 등급 평가 집합(DSN-49)을 자르는 자리다.
    """
    return fold_rows(rows, center, radius_m).candidates


def fold_rows(
    rows: Sequence[Mapping[str, Any]],
    center: LatLng,
    radius_m: float,
) -> FoldResult:
    """`fold_candidates` 와 같은 일을 하되 **떨어뜨린 행까지** 돌려준다(보고서용)."""
    folded: dict[str, list[tuple[float, float, float, int]]] = {}
    dropped: list[DroppedRow] = []
    for row in rows:
        parsed = _parse_row(row)
        if parsed is None:
            dropped.append(DroppedRow(_row_qid(row), "malformed_row", repr(row)[:120]))
            continue
        qid, lat, lng, sitelinks = parsed
        distance = haversine_m(center, LatLng(lat, lng))
        folded.setdefault(qid, []).append((distance, lat, lng, sitelinks))

    candidates: list[Candidate] = []
    for qid, coords in folded.items():
        # 중심 최근접. 동률이면 `(lat, lng)` 사전순 — 같은 거리의 두 좌표에서도 한 값이 정해진다.
        distance, lat, lng, _ = min(coords, key=lambda item: (item[0], item[1], item[2]))
        sitelinks = max(item[3] for item in coords)
        if distance > radius_m:
            dropped.append(DroppedRow(qid, "out_of_radius", f"{distance:.1f}m > {radius_m:.1f}m"))
            continue
        candidates.append(Candidate(qid, LatLng(lat, lng), sitelinks, distance, len(coords)))

    candidates.sort(key=lambda c: (-c.sitelinks, c.qid))
    return FoldResult(tuple(candidates), tuple(dropped))


def dedupe_by_name(spots: Sequence[Mapping[str, Any]]) -> NameDedupeResult:
    """같은 표기의 다른 QID 를 접는다 — `sitelinks` 가 큰 쪽만 남는다 (AC-065 "중복 이름 없음").

    비교는 공백만 정규화한 표기로 한다. 더 센 정규화(괄호 제거 등)를 쓰면 '○○ 박물관
    (신관)' 같은 **실제로 다른 곳**이 사라진다 — 그쪽이 더 조용한 오답이다.
    탈락분은 보고서에 남는다(`duplicate_name`).
    """
    ordered = sorted(spots, key=lambda spot: (-_sitelinks_of(spot), _qid_of(spot)))
    kept: list[Mapping[str, Any]] = []
    seen: dict[str, str] = {}
    dropped: list[DroppedRow] = []
    for spot in ordered:
        key = " ".join(str(spot.get("name", "")).split())
        if key and key in seen:
            dropped.append(DroppedRow(_qid_of(spot), "duplicate_name", f"{key} ← {seen[key]}"))
            continue
        if key:
            seen[key] = _qid_of(spot)
        kept.append(spot)
    return NameDedupeResult(tuple(kept), tuple(dropped))


def _parse_row(row: Mapping[str, Any]) -> tuple[str, float, float, int] | None:
    """SPARQL 행 하나 → `(qid, lat, lng, sitelinks)`. 읽을 수 없으면 `None`.

    공급자 원문(`{"item": {"value": "http://www.wikidata.org/entity/Q243"}}`)과
    평탄화한 형태(`{"qid": "Q243"}`) 둘 다 받는다 — 어댑터가 어느 쪽으로 넘겨도
    같은 판정이 나오는 편이, 두 자리에서 파싱이 갈리는 것보다 낫다.
    """
    qid = _row_qid(row)
    if not qid:
        return None
    try:
        lat = float(_scalar(row, "lat"))
        lng = float(_scalar(row, "lng"))
        sitelinks = int(float(_scalar(row, "sitelinks")))
    except (TypeError, ValueError):
        return None
    return qid, lat, lng, sitelinks


def _row_qid(row: Mapping[str, Any]) -> str:
    raw = _scalar(row, "qid")
    if raw in (None, ""):
        raw = _scalar(row, "item")
    if raw in (None, ""):
        raw = _scalar(row, "wikidata_id")
    return str(raw).rsplit("/", 1)[-1] if raw not in (None, "") else ""


def _scalar(row: Mapping[str, Any], key: str) -> Any:
    value = row.get(key)
    if isinstance(value, Mapping):  # SPARQL JSON 바인딩: {"type": "literal", "value": "..."}
        return value.get("value")
    return value


def _sitelinks_of(spot: Mapping[str, Any]) -> int:
    importance = spot.get("importance")
    if isinstance(importance, Mapping):
        return int(importance.get("sitelinks", 0) or 0)
    return int(spot.get("sitelinks", 0) or 0)


def _qid_of(spot: Mapping[str, Any]) -> str:
    return str(spot.get("wikidata_id") or spot.get("qid") or spot.get("id") or "")
