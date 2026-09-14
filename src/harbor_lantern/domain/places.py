"""근처 장소 정규화 · 중복 제거 · 거리순 정렬 — DSN-26 (REQ-017 · AC-050 · AC-051 · AC-056).

**순수 계산만 있다.** OSM(Overpass) 응답의 원소는 평범한 dict 로 들어오고, 나오는 것은
`Place` 튜플이다 — HTTP 도 DB 도 시계도 없다(`tests/static/test_layering.py` 가 검사한다).

거리와 길찾기 URL 은 새로 구현하지 않고 `domain/geo.py` 를 그대로 쓴다. 같은 규칙이
클라이언트에도 존재하므로(§6.3), 여기서 따로 만들면 화면과 API 가 다른 거리를 말한다.

**중복 제거 규칙(AC-056)은 명시적이다.** OSM 은 같은 가게를 노드(POI 점)와 웨이(건물
윤곽)로 **둘 다** 들고 있는 일이 흔하다 — 실측에서도 침사추이의 McDonald's 가 노드와
웨이로 같이 왔다. 그래서 `(정규화한 이름)` 이 같고 `MERGE_WITHIN_M` 안에 있으면 한 장소로
본다. 이름만으로 합치면 100m 떨어진 같은 체인의 다른 지점이 사라지고, 좌표만으로 합치면
한 건물에 든 다른 가게가 사라진다 — 둘 다 조용히 틀린다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from harbor_lantern.domain.geo import haversine_m
from harbor_lantern.domain.models import LatLng

__all__ = [
    "MERGE_WITHIN_M",
    "Place",
    "dedupe_places",
    "element_to_place",
    "normalize_name",
    "normalize_places",
    "sort_by_distance",
]

# 같은 이름이 이 거리 안에 있으면 한 장소로 본다. 노드 POI 와 그 건물 웨이의 중심은
# 보통 수 미터~수십 미터 떨어진다 — 실측(침사추이)에서 가장 먼 짝이 30m 이내였다.
MERGE_WITHIN_M = 60.0

# `osm_type` 정렬 우선순위. 노드가 가게 자신이고 웨이·릴레이션은 건물 윤곽인 경우가
# 많으므로, 중복이면 노드를 남긴다.
_TYPE_RANK = {"node": 0, "way": 1, "relation": 2}


@dataclass(frozen=True)
class Place:
    """정규화된 장소 한 건. 공급자 원문이 아니라 **우리 형태**다(§6.13 과 같은 이유).

    `osm_type`·`osm_id` 는 화면의 키이자 중복 판정의 타이브레이커다. 좌표는 반올림하지
    않는다 — 길찾기 딥링크가 그 값을 그대로 쓴다(AC-034).
    """

    osm_type: str
    osm_id: int
    name: str
    coord: LatLng
    category: str

    @property
    def key(self) -> str:
        """`'node/762014634'` — 공급자가 주는 유일 식별자."""
        return f"{self.osm_type}/{self.osm_id}"


def normalize_name(name: str) -> str:
    """중복 판정에 쓰는 이름. 대소문자·연속 공백만 지운다.

    더 영리하게(괄호 제거·약칭 통일) 만들면 서로 다른 가게가 합쳐지기 시작한다 —
    사라진 가게는 화면에 아무 표시도 남기지 않는다.
    """
    return " ".join(str(name).split()).casefold()


def _coord_of(element: Mapping[str, Any]) -> LatLng | None:
    """노드는 `lat`/`lon`, 웨이·릴레이션은 `center.lat`/`center.lon` 을 쓴다.

    (Overpass `out center` 가 웨이·릴레이션에 중심점을 붙여 준다 — 2026-09-14 실측 확인.)
    """
    center = element.get("center")
    source: Any = center if isinstance(center, Mapping) else element
    lat, lng = source.get("lat"), source.get("lon")
    if isinstance(lat, bool) or isinstance(lng, bool):
        return None
    if not isinstance(lat, int | float) or not isinstance(lng, int | float):
        return None
    if not (-90.0 <= float(lat) <= 90.0) or not (-180.0 <= float(lng) <= 180.0):
        return None
    return LatLng(float(lat), float(lng))


def element_to_place(
    element: Mapping[str, Any],
    tag_index: Mapping[tuple[str, str], str],
) -> Place | None:
    """OSM 원소 하나 → `Place`. 쓸 수 없으면 `None` (예외를 던지지 않는다).

    버리는 것: **이름이 없는 원소**(AC-056 — 지도에 "무명 식당"을 띄울 수는 없다),
    좌표가 없거나 범위 밖인 원소, 우리가 요청하지 않은 태그를 단 원소.
    """
    if not isinstance(element, Mapping):
        return None
    tags = element.get("tags")
    if not isinstance(tags, Mapping):
        return None

    raw_name = tags.get("name") or tags.get("name:ko") or tags.get("name:en")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None

    category = None
    for (key, value), name in tag_index.items():
        if tags.get(key) == value:
            category = name
            break
    if category is None:
        return None

    coord = _coord_of(element)
    if coord is None:
        return None

    osm_type = element.get("type")
    osm_id = element.get("id")
    if not isinstance(osm_type, str) or isinstance(osm_id, bool) or not isinstance(osm_id, int):
        return None

    return Place(osm_type=osm_type, osm_id=osm_id, name=raw_name.strip(), coord=coord, category=category)


def normalize_places(
    elements: Iterable[Mapping[str, Any]],
    tag_index: Mapping[tuple[str, str], str],
) -> tuple[Place, ...]:
    """원소 목록 → 정규화된 장소들. 읽을 수 없는 원소는 조용히 빠진다."""
    out: list[Place] = []
    for element in elements:
        place = element_to_place(element, tag_index)
        if place is not None:
            out.append(place)
    return tuple(out)


def dedupe_places(places: Sequence[Place], *, merge_within_m: float = MERGE_WITHIN_M) -> tuple[Place, ...]:
    """이름이 같고 `merge_within_m` 안에 있으면 하나만 남긴다 (AC-056).

    남는 쪽은 `node` → `way` → `relation` 순, 같은 종류면 `osm_id` 가 작은 쪽이다.
    입력 순서와 무관하게 **같은 결과**가 나온다 — 캐시에 들어가는 값이라 결정론이 필요하다.
    """
    kept: list[Place] = []
    ordered = sorted(places, key=lambda p: (normalize_name(p.name), _TYPE_RANK.get(p.osm_type, 9), p.osm_id))
    by_name: dict[str, list[Place]] = {}
    for place in ordered:
        name_key = normalize_name(place.name)
        group = by_name.setdefault(name_key, [])
        if any(haversine_m(place.coord, other.coord) <= merge_within_m for other in group):
            continue
        group.append(place)
        kept.append(place)
    # 안정적인 최종 순서(정렬은 호출자가 거리로 다시 한다).
    return tuple(sorted(kept, key=lambda p: (_TYPE_RANK.get(p.osm_type, 9), p.osm_id)))


def sort_by_distance(places: Iterable[Place], origin: LatLng) -> tuple[tuple[Place, float], ...]:
    """`(장소, 거리m)` 을 **거리 오름차순**으로 (AC-051).

    동점은 이름 → `osm_type` → `osm_id` 로 깨뜨린다. 그렇게 하지 않으면 같은 입력에
    다른 순서가 나올 수 있고, 그건 새로고침마다 목록이 흔들리는 것으로 보인다.
    """
    measured = [(place, haversine_m(origin, place.coord)) for place in places]
    measured.sort(key=lambda item: (item[1], item[0].name, _TYPE_RANK.get(item[0].osm_type, 9), item[0].osm_id))
    return tuple(measured)
