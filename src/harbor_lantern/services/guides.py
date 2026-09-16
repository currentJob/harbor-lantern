"""구운 도시 가이드 런타임 로더 — DSN-41 · DSN-44 (설계서 §16.12 · §16.15).

**`services/curated.py` 와 같은 형태다** — 외부 호출도, TTL 도, stale 폴백도 없다.
파일을 읽어 메모리에 둔다. 이 모듈이 하는 I/O 는 **파일 읽기 하나뿐**이며 그것이
NFR-017(런타임 외부 호출 0건)을 구조적으로 보장하는 자리다. 조사는 빌드 타임
베이커(`tools/`)가 이미 끝냈고, 여기서는 그 결과를 읽기만 한다 — 파리 한 도시를
런타임에 조사하려다 76초 만에 504 를 낸 것이 이 분리의 이유다(`docs/_recon.md`).

**배치는 인덱스 1 + 도시별 파일 N 이다**(§16.11 · O14). 국가 목록 한 번 조회에 도시
30개의 스팟과 설명이 전부 메모리로 올라오지 않게 하기 위해서다 — 인덱스만 상주하고
도시 파일은 요청된 것만 올라온다.

**아직 굽지 않은 상태가 정상이다.** `seed/city-guides/` 는 베이커가 만든다. 디렉터리도
파일도 없을 수 있고, 그때 이 모듈은 **조용히 빈 상태로 동작한다** — 없는 데이터 때문에
서버가 뜨지 못하면 폴백 경로(REQ-028)까지 같이 죽는다. 다만 *있는데 깨진* 파일은
숨기지 않는다(JSON 오류는 그대로 올라간다). 없는 것과 망가진 것은 다른 사건이다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "CITY_ID_PATTERN",
    "DATA_DIR",
    "CityGuide",
    "GuideIndex",
    "find_cities",
    "is_city_id",
    "load_city",
    "load_index",
]

# `src/harbor_lantern/services/guides.py` → parents[3] 이 프로젝트 루트다
# (`curated.py`·`storage/seed.py` 와 같은 규칙).
DATA_DIR = Path(__file__).resolve().parents[3] / "seed" / "city-guides"

# 식별자는 **검증한 뒤에만** 경로에 붙인다. 검증 없이 붙이면 `../` 가 파일 읽기가 된다.
CITY_ID_PATTERN = r"^[a-z0-9-]+$"
_CITY_ID = re.compile(CITY_ID_PATTERN)


@dataclass(frozen=True)
class GuideIndex:
    """데이터셋 메타 + 도시 요약 목록. 작고 자주 읽힌다(§16.11)."""

    dataset: str = ""
    retrieved_at: str = ""
    what_this_is: str = ""
    sources: tuple[dict[str, Any], ...] = ()
    known_gaps: tuple[str, ...] = ()
    counts: dict[str, Any] = field(default_factory=dict)
    cities: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CityGuide:
    """도시 하나의 전체 가이드. 그 도시를 고를 때만, 크고 드물게 읽힌다(§16.11)."""

    city_id: str
    name_ko: str
    name_local: str
    name_en: str
    country_code: str
    country_ko: str
    country_en: str
    center: dict[str, float]
    radius_m: int
    grade: str
    retrieved_at: str
    harvest: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    known_gaps: tuple[str, ...]
    spots: tuple[dict[str, Any], ...]

    def as_city(self) -> dict[str, Any]:
        """`domain.guide.build_guide_plan` 이 읽는 도시 매핑 (§16.14)."""
        return {
            "city_id": self.city_id,
            "name_ko": self.name_ko,
            "center": dict(self.center),
            "grade": self.grade,
            "retrieved_at": self.retrieved_at,
            "sources": list(self.sources),
            "known_gaps": list(self.known_gaps),
        }


def is_city_id(value: str) -> bool:
    """`[a-z0-9-]+` 만 통과. 검증 실패는 404 이지 500 이 아니다(§16.12)."""
    return bool(_CITY_ID.match(value or ""))


@lru_cache(maxsize=1)
def load_index(path: str | None = None) -> GuideIndex:
    """`<path>/index.json`. 없으면 **빈 인덱스**다 — 예외를 던지지 않는다.

    `path` 는 가이드 **디렉터리**다(파일이 아니다). 테스트는 `tmp_path` 를 준다.
    프로세스 수명 동안 한 번만 읽는다 — 굽힌 파일은 런타임에 바뀌지 않는다.
    """
    target = (Path(path) if path else DATA_DIR) / "index.json"
    document = _read_json(target)
    if document is None:
        return GuideIndex()
    return GuideIndex(
        dataset=str(document.get("dataset", "")),
        retrieved_at=str(document.get("retrieved_at", "")),
        what_this_is=str(document.get("what_this_is", "")),
        sources=tuple(document.get("sources", ())),
        known_gaps=tuple(document.get("known_gaps", ())),
        counts=dict(document.get("counts", {})),
        cities=tuple(document.get("cities", ())),
    )


@lru_cache(maxsize=32)
def load_city(city_id: str, path: str | None = None) -> CityGuide | None:
    """`<path>/<city_id>.json`. 굽지 않은 도시면 `None` — 호출자는 폴백 경로로 간다(REQ-028).

    상한 32 는 도시 30개 규모에서 전부 상주해도 텍스트뿐이라는 계산이고, 그래도 상한을
    두는 이유는 데이터가 늘었을 때 조용히 메모리를 먹지 않게 하기 위해서다.
    """
    if not is_city_id(city_id):
        return None
    target = (Path(path) if path else DATA_DIR) / f"{city_id}.json"
    document = _read_json(target)
    if document is None:
        return None
    center = document.get("center") or {}
    return CityGuide(
        city_id=str(document.get("city_id", city_id)),
        name_ko=str(document.get("name_ko", "")),
        name_local=str(document.get("name_local", "")),
        name_en=str(document.get("name_en", "")),
        country_code=str(document.get("country_code", "")),
        country_ko=str(document.get("country_ko", "")),
        country_en=str(document.get("country_en", "")),
        center={"lat": float(center.get("lat", 0.0)), "lng": float(center.get("lng", 0.0))},
        radius_m=int(document.get("radius_m", 0) or 0),
        grade=str(document.get("grade", "")),
        retrieved_at=str(document.get("retrieved_at", "")),
        harvest=dict(document.get("harvest", {})),
        sources=tuple(document.get("sources", ())),
        known_gaps=tuple(document.get("known_gaps", ())),
        spots=tuple(document.get("spots", ())),
    )


def find_cities(
    query: str | None = None,
    country_code: str | None = None,
    index: GuideIndex | None = None,
) -> list[dict[str, Any]]:
    """국가·광역 입력 — DSN-44 (§16.15 · AC-075 · AC-076).

    **"일본"·"Japan"·"JP" 가 같은 목록을 준다.** 정규화는 NFKC → 공백 제거 → casefold 고,
    국가는 `country_ko`·`country_en`·`country_code` 의 **완전 일치**만 본다. 국가명에
    부분 일치를 허용하면 "미국"이 "미국령 사모아"를 끌어오는 조용한 오답이 생긴다.
    국가로 맞는 것이 없을 때만 도시 이름(`name_ko`·`name_en`·`name_local`)의 접두 일치를 본다.

    결과 항목은 인덱스 항목 그대로다 — `city_id`·`name_ko`·`center`·`grade` 를 담고
    있으므로 **그대로 일정 생성 입력**으로 쓸 수 있다(AC-075).
    구운 도시가 없으면 **빈 목록**이다. "없음"은 오류가 아니다(AC-076).
    """
    rows = list((index or load_index()).cities)

    if country_code:
        wanted = _fold(country_code)
        rows = [city for city in rows if _fold(str(city.get("country_code", ""))) == wanted]

    if query:
        wanted = _fold(query)
        if wanted:
            matched = [city for city in rows if wanted in _country_keys(city)]
            if not matched:
                matched = [
                    city
                    for city in rows
                    if any(name.startswith(wanted) for name in _city_names(city) if name)
                ]
            rows = matched

    return sorted((dict(city) for city in rows), key=lambda city: str(city.get("city_id", "")))


def _read_json(target: Path) -> dict[str, Any] | None:
    """없으면 `None`. **깨진 파일은 숨기지 않는다** — JSON 오류는 그대로 올라간다."""
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    document = json.loads(text)
    return document if isinstance(document, dict) else None


def _fold(value: str) -> str:
    """NFKC → 공백 제거 → casefold (§16.15)."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return "".join(normalized.split()).casefold()


def _country_keys(city: Mapping[str, Any]) -> set[str]:
    return {_fold(str(city.get(key, ""))) for key in ("country_ko", "country_en", "country_code")} - {""}


def _city_names(city: Mapping[str, Any]) -> list[str]:
    return [_fold(str(city.get(key, ""))) for key in ("name_ko", "name_en", "name_local")]
