"""큐레이션 목록 — 미쉐린 홍콩·마카오 (REQ-019 · DSN-28).

**이것은 근처 검색(`/api/nearby`)과 다른 성격의 데이터다.**

- `nearby` 는 지금 내 주변에 *무엇이 있는가* — OSM 에서 그때그때 조회한다.
- 여기는 *어디가 좋은가* — 미리 조사해 저장소에 넣어 둔 고정 목록이다.

그래서 외부 호출도, TTL 캐시도, stale 폴백도 없다. 파일 하나를 읽어 메모리에 둔다.

**좌표가 없는 항목이 정상이다.** 상당수 식당이 호텔 안에 있어 이름만으로는 위치를
가를 수 없었다(같은 이름의 다른 가게·공항 지점이 잡혔다). 확인 못 한 곳은 좌표를
비워 두기로 했고, 이 모듈은 그 사실을 감추지 않는다 — 거리 정렬은 좌표가 있는
항목에만 적용하고, 나머지는 목록 뒤쪽에 그대로 남긴다. 목록에서 빼면 사용자는
"홍콩에 미쉐린 1스타가 11곳뿐"이라고 잘못 알게 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng

__all__ = ["CuratedDataset", "load_curated", "select"]

# `src/harbor_lantern/services/curated.py` → parents[3] 이 프로젝트 루트다
# (`storage/seed.py` 와 같은 규칙).
DATA_PATH = Path(__file__).resolve().parents[3] / "seed" / "curated-places.json"


@dataclass(frozen=True)
class CuratedDataset:
    dataset: str
    retrieved_at: str
    what_this_is: str
    sources: tuple[dict[str, Any], ...]
    known_gaps: tuple[str, ...]
    counts: dict[str, Any]
    places: tuple[dict[str, Any], ...]


@lru_cache(maxsize=1)
def load_curated(path: str | None = None) -> CuratedDataset:
    """데이터셋을 읽는다. 프로세스 수명 동안 한 번만 읽는다(고정 파일이다)."""
    target = Path(path) if path else DATA_PATH
    document = json.loads(target.read_text(encoding="utf-8"))
    return CuratedDataset(
        dataset=document.get("dataset", ""),
        retrieved_at=document.get("retrieved_at", ""),
        what_this_is=document.get("what_this_is", ""),
        sources=tuple(document.get("sources", ())),
        known_gaps=tuple(document.get("known_gaps", ())),
        counts=dict(document.get("counts", {})),
        places=tuple(document.get("places", ())),
    )


def select(
    dataset: CuratedDataset,
    *,
    city: str | None = None,
    min_stars: int = 1,
    origin: LatLng | None = None,
) -> list[dict[str, Any]]:
    """필터 + 정렬.

    `origin` 을 주면 **좌표가 있는 항목만** 거리를 계산해 가까운 순으로 올린다.
    좌표가 없는 항목은 거리 `None` 으로 뒤에 붙는다 — 빼지 않는다(위 주석).
    """
    rows: list[dict[str, Any]] = []
    for place in dataset.places:
        if city and place.get("city") != city:
            continue
        if int(place.get("stars") or 0) < min_stars:
            continue

        distance: float | None = None
        lat, lng = place.get("lat"), place.get("lng")
        if origin is not None and lat is not None and lng is not None:
            distance = haversine_m(origin, LatLng(float(lat), float(lng)))
        rows.append({**place, "distance_m": distance})

    if origin is not None:
        # 좌표 없는 항목(distance None)을 뒤로. 파이썬은 None 과 float 을 비교하지
        # 못하므로 키를 둘로 나눈다 — 한 키에 섞으면 TypeError 로 터진다.
        rows.sort(key=lambda r: (r["distance_m"] is None, r["distance_m"] or 0.0,
                                 -int(r.get("stars") or 0), r.get("name") or ""))
    else:
        rows.sort(key=lambda r: (r.get("city") or "", -int(r.get("stars") or 0),
                                 r.get("name") or ""))
    return rows
