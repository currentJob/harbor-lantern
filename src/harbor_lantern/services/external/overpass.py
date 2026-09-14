"""근처 장소 어댑터 — OpenStreetMap Overpass API (DSN-26 · REQ-017).

API 키가 필요 없다(NFR-011 — 소스에 키 문자열이 없다).

**출처와 실측 (조회일 2026-09-14)**

출처 [1] https://wiki.openstreetmap.org/wiki/Overpass_API
출처 [2] https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL

| | 확인한 것 | 출처 |
|---|---|---|
| 엔드포인트 | `POST .../api/interpreter` (본문 `data=<쿼리>`) | [1] |
| 반경 필터 | `(around:반경m,위도,경도)` | [2] |
| 노드·웨이·릴레이션 동시 질의 | `nwr` | [2] |
| 웨이·릴레이션 중심 좌표 | `out center` (바운딩박스 중심을 붙여 준다) | [2] |
| 결과 개수 상한 | `out [verbosity] [limit];` (예: `out center 50;`) | [2] |

실측(2026-09-14, 침사추이 22.2937/114.1730 · 반경 800m · restaurant|cafe|fast_food):
`out center 50` 으로 200 OK · 50건(전부 이름 있음), 같은 좌표에 `way` 만 질의하면
`{"type":"way","id":520728661,"center":{"lat":22.3000446,"lon":114.1703825}, ...}` 가
돌아온다 — 웨이에도 중심 좌표가 붙는다는 것을 이 호출로 확인했다.

**같은 실측에서 429 도 받았다.** 짧은 간격으로 두 번 질의하자 공용 인스턴스가 거절했다.
그래서 이 어댑터는 (1) 개수·반경 상한을 스스로 강제하고 (2) 예의 있는 User-Agent 를 보내며
(3) 캐시(`nearby.py`) 뒤에서만 불린다. 무료 공용 서비스에 매 요청을 그대로 흘려보내면
먼저 막히는 것은 우리 사용자다.

**테스트에서 실제로 호출되면 안 된다.** `tests/conftest.py` 의 네트워크 차단이 세션
전역이라 아웃바운드는 그 자리에서 실패한다. 어댑터 테스트는 `client` 인자로
`httpx.MockTransport` 를 꽂아 응답 샘플을 주입한다(NFR-003 · AC-037).
"""

from __future__ import annotations

from typing import Any

import httpx

from harbor_lantern.config import NEARBY_CATEGORIES, NearbyConfig, nearby_tag_index
from harbor_lantern.domain.places import dedupe_places, normalize_places
from harbor_lantern.services.external.ports import ExternalUnavailable, PlaceSnapshot

__all__ = ["OverpassNearbyAdapter", "build_query"]

# 서버 쪽 질의 타임아웃(초). Overpass QL 헤더의 `[timeout:...]` 이며 HTTP 타임아웃과
# 다른 값이다 — 우리 쪽이 먼저 끊더라도 남의 서버가 무한히 도는 일을 막는다.
_QUERY_TIMEOUT_S = 25


def build_query(categories: tuple[str, ...], lat: float, lng: float, radius_m: int, limit: int) -> str:
    """Overpass QL 한 덩어리. 카테고리를 **한 번의 질의**로 묶는다.

    카테고리마다 따로 부르면 호출 수가 카테고리 수만큼 늘어난다 — 공용 서비스에 대한
    예의이기도 하고, 캐시 항목이 쪼개지지 않게 하는 조건이기도 하다(AC-054).
    """
    by_key: dict[str, list[str]] = {}
    for name in categories:
        category = NEARBY_CATEGORIES[name]
        by_key.setdefault(category.osm_key, []).extend(category.osm_values)

    clauses = []
    for key, values in by_key.items():
        pattern = "|".join(sorted(set(values)))
        clauses.append(f'  nwr(around:{radius_m},{lat},{lng})["{key}"~"^({pattern})$"];')
    body = "\n".join(clauses)
    return f"[out:json][timeout:{_QUERY_TIMEOUT_S}];\n(\n{body}\n);\nout center {limit};\n"


class OverpassNearbyAdapter:
    """`NearbyPort` 구현. 아웃바운드 HTTP 는 `services/external/` 안에서만 일어난다(§2.2)."""

    def __init__(self, cfg: NearbyConfig, client: httpx.Client | None = None) -> None:
        self._cfg = cfg
        self._client = client

    def fetch(
        self,
        *,
        categories: tuple[str, ...],
        lat: float,
        lng: float,
        radius_m: int,
    ) -> tuple[PlaceSnapshot, ...]:
        if not categories:
            # 카테고리가 비면 "전부"가 아니라 "아무것도" 다. 빈 질의를 보내면 Overpass 가
            # 반경 안의 모든 것을 긁는다 — 절대 보내지 않는다.
            raise ExternalUnavailable("카테고리가 비어 있다")

        # 어댑터도 상한을 스스로 강제한다. API 계층이 이미 막지만, 이 클래스는 다른
        # 호출자(도구·스크립트)에게도 열려 있다 — 경계는 한 겹이면 언젠가 새어 나간다.
        radius = max(1, min(int(radius_m), self._cfg.max_radius_m))
        limit = max(1, int(self._cfg.max_results))
        query = build_query(categories, lat, lng, radius, limit)
        headers = {"User-Agent": self._cfg.user_agent, "Accept": "application/json"}

        try:
            if self._client is not None:
                response = self._client.post(self._cfg.url, data={"data": query}, headers=headers)
            else:
                with httpx.Client(timeout=self._cfg.timeout_s) as client:
                    response = client.post(self._cfg.url, data={"data": query}, headers=headers)
            response.raise_for_status()
            document = response.json()
        except Exception as exc:  # 타임아웃 · 429 · 5xx · JSON 파싱 실패 전부 한 종류로 좁힌다
            raise ExternalUnavailable(f"overpass 호출 실패: {exc}") from exc

        return _to_snapshots(document, categories, limit)


def _to_snapshots(document: Any, categories: tuple[str, ...], limit: int) -> tuple[PlaceSnapshot, ...]:
    if not isinstance(document, dict):
        raise ExternalUnavailable("overpass 응답이 객체가 아니다")
    elements = document.get("elements")
    if not isinstance(elements, list):
        raise ExternalUnavailable("overpass 응답에 elements 가 없다")

    # 정규화·중복 제거는 순수 도메인에서 한다(§2.2). **빈 결과는 실패가 아니다** —
    # 사막 한가운데에는 정말로 식당이 없고, 그걸 실패로 보면 stale 폴백이 엉뚱한
    # 동네의 목록을 계속 보여 준다.
    places = dedupe_places(normalize_places(elements, nearby_tag_index(categories)))
    return tuple(
        PlaceSnapshot(
            osm_type=place.osm_type,
            osm_id=place.osm_id,
            name=place.name,
            lat=place.coord.lat,
            lng=place.coord.lng,
            category=place.category,
        )
        for place in places[:limit]
    )
