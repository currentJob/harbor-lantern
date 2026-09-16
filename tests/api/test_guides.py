"""구운 도시 가이드 — 로더와 API 표면 (DSN-41 · DSN-44 · DSN-45).

**실제 `seed/city-guides/` 에 의존하지 않는다.** 그 디렉터리는 베이커(T8)가 만들고, 언제
무엇이 들어 있는지는 굽기 실행에 달렸다. 데이터가 바뀔 때마다 빨개지는 테스트는 곧
아무도 안 보는 테스트가 된다 — 그래서 여기는 `tmp_path` 에 만든 **고정 픽스처**로만 잰다.

아직 굽지 않은 상태(디렉터리 없음)도 정상 입력이다. 그 경우가 오늘의 저장소다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harbor_lantern.services import guides

PARIS_SPOTS = [
    ("Q243", "에펠탑", 48.8584, 2.2945, "monument", 191, "7구", True),
    ("Q19675", "루브르 박물관", 48.8606, 2.3376, "museum", 169, "1구", False),
    ("Q2981", "노트르담 대성당", 48.8530, 2.3499, "worship", 125, "4구", False),
    ("Q83125", "개선문", 48.8738, 2.2950, "monument", 98, "8구", False),
    ("Q23402", "오르세 미술관", 48.8600, 2.3266, "museum", 87, "7구", False),
    ("Q1189960", "사크레쾨르 대성당", 48.8867, 2.3431, "worship", 76, "18구", True),
    ("Q207694", "퐁피두 센터", 48.8607, 2.3522, "gallery", 71, "4구", False),
    ("Q188507", "뤽상부르 공원", 48.8462, 2.3372, "park", 64, "6구", False),
    ("Q160236", "팡테옹", 48.8462, 2.3464, "monument", 58, "5구", False),
    ("Q193878", "생트샤펠", 48.8554, 2.3450, "worship", 52, "1구", False),
    ("Q1130591", "몽파르나스 타워", 48.8422, 2.3220, "tower", 47, "15구", True),
    ("Q207116", "튈르리 정원", 48.8634, 2.3275, "park", 41, "1구", False),
    ("Q1063814", "그랑 팔레", 48.8661, 2.3125, "gallery", 38, "8구", False),
    ("Q1145791", "레 알", 48.8622, 2.3449, "monument", 33, "1구", False),
    ("Q1234394", "생제르맹데프레", 48.8540, 2.3336, "worship", 29, "6구", False),
    ("Q1139149", "바스티유 광장", 48.8532, 2.3692, "monument", 26, "11구", False),
    ("Q1143767", "퐁뇌프", 48.8570, 2.3412, "bridge", 22, "1구", False),
    ("Q1234567", "벼룩시장", 48.9012, 2.3416, "monument", 18, "18구", False),
]

KYOTO_SPOTS = [
    ("Q1192252", "기요미즈데라", 34.9949, 135.7850, "worship", 64, "히가시야마구", False),
    ("Q1140898", "긴카쿠지", 35.0270, 135.7982, "worship", 55, "사쿄구", False),
    ("Q1140899", "니조 성", 35.0142, 135.7481, "monument", 44, "나카교구", False),
    ("Q1140900", "후시미이나리", 34.9671, 135.7727, "worship", 39, "후시미구", False),
    ("Q1140901", "교토 타워", 34.9875, 135.7592, "tower", 21, "시모교구", True),
    ("Q1140902", "교토 국립박물관", 34.9900, 135.7730, "museum", 17, "히가시야마구", False),
    ("Q1140903", "가모강 공원", 35.0116, 135.7720, "park", 12, "", False),
    ("Q1140904", "산주산겐도", 34.9879, 135.7726, "worship", 11, "히가시야마구", False),
    ("Q1140905", "니시키 시장", 35.0050, 135.7649, "monument", 10, "나카교구", False),
]


def _spot(row: tuple[Any, ...]) -> dict[str, Any]:
    qid, name, lat, lng, category, sitelinks, area, evening = row
    return {
        "id": f"wd:{qid}",
        "wikidata_id": qid,
        "name": name,
        "name_source": "ko",
        "name_original": name,
        "lat": lat,
        "lng": lng,
        "category": category,
        "wikidata_classes": [],
        "importance": {"sitelinks": sitelinks},
        "area": area,
        "description": f"{name} 설명." if sitelinks > 20 else "",
        "description_source": (
            {
                "url": f"https://ko.wikipedia.org/wiki/{name}",
                "title": name,
                "license": "CC BY-SA 4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "retrieved_at": "2026-09-15",
            }
            if sitelinks > 20
            else None
        ),
        "verification": {"status": "passed", "coord_delta_m": 10.0, "label_match": "exact",
                         "redirected": False, "reason": ""},
        "hours_text": "",
        "hours_source": None,
        "evening_candidate": evening,
        "tips": [],
        "recommendations": [],
    }


def _city_document(city_id: str, names: dict[str, str], center: dict[str, float],
                   grade: str, rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    spots = [_spot(row) for row in rows]
    return {
        "schema_version": 1,
        "city_id": city_id,
        **names,
        "center": center,
        "radius_m": 6000,
        "retrieved_at": "2026-09-15",
        "grade": grade,
        "query": {"sitelink_min": 25, "limit": 150, "radius_m": 6000},
        "harvest": {"candidate_count": 100, "spot_count": len(spots), "grade_window": len(spots),
                    "ko_label_count": len(spots), "ko_label_ratio": 1.0},
        "sources": [{"what": "Wikidata", "url": "https://query.wikidata.org/",
                     "retrieved_at": "2026-09-15"}],
        "known_gaps": ["설명이 없는 스팟이 있다 — 한국어 문서가 없다."],
        "spots": spots,
    }


@pytest.fixture
def baked(tmp_path: Path) -> Path:
    """구운 결과 두 도시 — 프랑스(완전) · 일본(부분)."""
    directory = tmp_path / "city-guides"
    directory.mkdir()
    paris = _city_document(
        "paris",
        {"name_ko": "파리", "name_local": "Paris", "name_en": "Paris",
         "country_code": "FR", "country_ko": "프랑스", "country_en": "France"},
        {"lat": 48.8566, "lng": 2.3522}, "full", PARIS_SPOTS,
    )
    kyoto = _city_document(
        "kyoto",
        {"name_ko": "교토", "name_local": "京都市", "name_en": "Kyoto",
         "country_code": "JP", "country_ko": "일본", "country_en": "Japan"},
        {"lat": 35.0116, "lng": 135.7681}, "partial", KYOTO_SPOTS,
    )
    for document in (paris, kyoto):
        (directory / f"{document['city_id']}.json").write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
            encoding="utf-8", newline="\n",
        )
    index = {
        "dataset": "city-guides",
        "retrieved_at": "2026-09-15",
        "what_this_is": "테스트 픽스처",
        "sources": [{"what": "Wikidata", "url": "https://query.wikidata.org/",
                     "retrieved_at": "2026-09-15"}],
        "known_gaps": ["두 도시뿐이다."],
        "counts": {"surveyed": 3, "exported": 2, "full": 1, "partial": 1, "below": 1},
        "cities": [
            {"city_id": document["city_id"], "name_ko": document["name_ko"],
             "name_local": document["name_local"], "name_en": document["name_en"],
             "country_code": document["country_code"], "country_ko": document["country_ko"],
             "country_en": document["country_en"], "center": document["center"],
             "grade": document["grade"], "spot_count": len(document["spots"]),
             "retrieved_at": document["retrieved_at"]}
            for document in (kyoto, paris)  # 인덱스 순서가 뒤여도 응답은 city_id 순이다
        ],
    }
    (directory / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8", newline="\n",
    )
    guides.load_index.cache_clear()
    guides.load_city.cache_clear()
    yield directory
    guides.load_index.cache_clear()
    guides.load_city.cache_clear()


class RefusingDiscovery:
    """가이드 경로에서 **한 번이라도 불리면 실패한다** (NFR-017 · AC-079)."""

    reviews_enabled = False

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("가이드 경로는 외부 공급자를 부르지 않는다 (NFR-017)")

    destinations = places = reviewed_restaurants = _refuse


@pytest.fixture
def guided(app: Any, client: Any, baked: Path) -> Any:
    app.state.guides_dir = str(baked)
    app.state.discovery = RefusingDiscovery()
    return client


# ── 로더 (DSN-41) ─────────────────────────────────────────────────────────
def test_missing_directory_is_an_empty_index_not_a_crash(tmp_path: Path) -> None:
    """굽기 전 저장소가 오늘의 상태다. 파일이 없다고 서버가 죽으면 폴백까지 같이 죽는다."""
    guides.load_index.cache_clear()
    guides.load_city.cache_clear()
    empty = guides.load_index(str(tmp_path / "없는디렉터리"))
    assert empty.cities == ()
    assert empty.counts == {}
    assert guides.load_city("paris", str(tmp_path / "없는디렉터리")) is None


def test_city_id_is_validated_before_it_touches_a_path(baked: Path) -> None:
    """`../` 가 파일 읽기가 되지 않는다 (§16.12). 검증 실패는 `None` 이지 예외가 아니다."""
    for bad in ("../index", "Paris", "pa ris", "paris/../index", ""):
        assert guides.load_city(bad, str(baked)) is None
    assert guides.load_city("paris", str(baked)) is not None


def test_loader_reads_the_baked_shape(baked: Path) -> None:
    guide = guides.load_city("paris", str(baked))
    assert guide is not None
    assert (guide.name_ko, guide.grade, guide.country_code) == ("파리", "full", "FR")
    assert guide.center == {"lat": 48.8566, "lng": 2.3522}
    assert len(guide.spots) == len(PARIS_SPOTS)
    assert guide.as_city()["city_id"] == "paris"


def test_country_input_is_three_spellings_of_the_same_thing(baked: Path) -> None:
    """AC-075 — "일본"·"Japan"·"JP" 가 같은 목록을 준다."""
    index = guides.load_index(str(baked))
    results = [guides.find_cities(term, None, index) for term in ("일본", "Japan", " jp ")]
    assert all(result == results[0] for result in results)
    assert [city["city_id"] for city in results[0]] == ["kyoto"]


def test_country_match_is_exact_not_partial(baked: Path) -> None:
    """부분 일치를 국가명에 허용하면 "미국"이 "미국령 사모아"를 끌어온다 (§16.15)."""
    index = guides.load_index(str(baked))
    assert guides.find_cities("일", None, index) == []
    assert [city["city_id"] for city in guides.find_cities("교토", None, index)] == ["kyoto"]


def test_find_cities_orders_by_city_id(baked: Path) -> None:
    index = guides.load_index(str(baked))
    assert [city["city_id"] for city in guides.find_cities(None, None, index)] == ["kyoto", "paris"]


# ── 목록 · 상세 (DSN-45) ──────────────────────────────────────────────────
def test_guides_list_returns_planning_ready_items(guided: Any) -> None:
    """AC-075 — 목록 항목을 그대로 일정 생성 입력으로 쓸 수 있다."""
    body = guided.get("/api/explore/guides?q=프랑스").json()
    assert [city["city_id"] for city in body["cities"]] == ["paris"]
    city = body["cities"][0]
    assert city["center"] == {"lat": 48.8566, "lng": 2.3522}
    assert city["grade"] == "full"
    assert body["counts"]["exported"] == 2
    assert body["sources"] and body["known_gaps"]


def test_unbaked_country_is_an_empty_list_with_a_notice(guided: Any) -> None:
    """AC-076 — "없음"은 오류가 아니다. 404 도 5xx 도 아니다."""
    response = guided.get("/api/explore/guides?country=KR")
    assert response.status_code == 200
    body = response.json()
    assert body["cities"] == []
    assert "제한된 자동 추천" in body["notice"]


def test_guides_list_works_before_anything_is_baked(app: Any, client: Any, tmp_path: Path) -> None:
    guides.load_index.cache_clear()
    app.state.guides_dir = str(tmp_path / "아직없다")
    response = client.get("/api/explore/guides")
    assert response.status_code == 200
    assert response.json()["cities"] == []
    guides.load_index.cache_clear()


def test_guide_detail_carries_sources_and_grade(guided: Any) -> None:
    body = guided.get("/api/explore/guides/paris").json()
    assert body["grade"] == "full"
    assert body["spot_count"] == len(PARIS_SPOTS)
    assert body["spots"][0]["description_source"]["license"] == "CC BY-SA 4.0"
    assert body["known_gaps"] and body["sources"]


def test_guide_detail_rejects_unbaked_and_malformed_ids(guided: Any) -> None:
    assert guided.get("/api/explore/guides/osaka").status_code == 404
    assert guided.get("/api/explore/guides/Paris").status_code == 422
    assert guided.get("/api/explore/guides/pa_ris").status_code == 422


# ── 일정 (DSN-45 · AC-077 · AC-085) ───────────────────────────────────────
def _plan_body(**overrides: Any) -> dict[str, Any]:
    return {"start_date": "2026-10-05", "end_date": "2026-10-07", **overrides}


def test_guided_plan_never_calls_a_provider(guided: Any) -> None:
    """AC-079 — 가이드 경로의 외부 호출은 0건이다. 공급자는 불리면 터지는 물건이다."""
    response = guided.post("/api/explore/plan", json=_plan_body(city_id="paris"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["days"]) == 3
    assert body["scheduled_count"] > 0


def test_guided_plan_keeps_the_top_spots_and_the_grade(guided: Any) -> None:
    """AC-068 — 파리 3일에 상위 스팟이 들어간다. AC-085 — 등급이 실린다."""
    body = guided.post("/api/explore/plan", json=_plan_body(city_id="paris")).json()
    names = [stop["place"]["name"] for day in body["days"] for stop in day["stops"]]
    assert {"에펠탑", "루브르 박물관", "노트르담 대성당"} <= set(names)
    assert body["guide_grade"] == "full"
    assert body["guide_city"]["city_id"] == "paris"
    assert "2026-09-15" in body["guide_notice"]
    assert body["days"][0]["title"] and body["days"][0]["color"]


def test_partial_city_says_partial(guided: Any) -> None:
    body = guided.post("/api/explore/plan", json=_plan_body(city_id="kyoto")).json()
    assert body["guide_grade"] == "partial"
    assert "부분 가이드" in body["guide_notice"]


def test_unbaked_city_falls_back_and_says_so(app: Any, client: Any, baked: Path) -> None:
    """REQ-028 · AC-077 — 굽지 않은 도시는 폴백하되 "제한된 자동 추천"이라고 밝힌다."""
    app.state.guides_dir = str(baked)

    class Provider(RefusingDiscovery):
        def places(self, lat, lng, radius, restaurants_only=False):  # noqa: ANN001, ANN201
            return [{"id": "one", "name": "표본", "lat": lat, "lng": lng, "category": "museum",
                     "opening_hours": "24/7", "rating": None, "reviews": []}]

    app.state.discovery = Provider()
    body = client.post("/api/explore/plan", json=_plan_body(
        city_id="osaka", destination={"name": "Osaka", "lat": 34.69, "lng": 135.50})).json()
    assert body["guide_grade"] == "heuristic"
    assert "제한된 자동 추천" in body["guide_notice"]
    assert body["guide_city"] is None


def test_three_grades_say_three_different_things(guided: Any) -> None:
    """AC-085 — 세 문구가 서로 다르지 않으면 등급을 싣는 의미가 없다."""
    notices = {
        guided.post("/api/explore/plan", json=_plan_body(city_id=city)).json()["guide_notice"]
        for city in ("paris", "kyoto")
    }
    from harbor_lantern.api.routes.explore import GRADE_NOTICE

    notices.add(GRADE_NOTICE["heuristic"])
    assert len(notices) == 3


def test_plan_requires_one_of_city_id_or_destination(guided: Any) -> None:
    assert guided.post("/api/explore/plan", json=_plan_body()).status_code == 422
    # 굽지 않은 도시 + 좌표 없음 = 폴백조차 만들 수 없다.
    assert guided.post("/api/explore/plan", json=_plan_body(city_id="osaka")).status_code == 422
    # 형식이 틀린 식별자는 Pydantic 이 먼저 잡는다.
    assert guided.post("/api/explore/plan", json=_plan_body(city_id="Paris")).status_code == 422
