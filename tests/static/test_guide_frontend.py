"""가이드 화면의 순수 함수 실행 검증 — DSN-46 (AC-069 · AC-070 · AC-072 · AC-077 · AC-080 · AC-085).

`test_frontend_pure_functions.py` 와 같은 방식이다 — **실제로 배포되는 그 파일을** Node 로
import 해서 돌린다. 파이썬으로 같은 문자열을 다시 적어 비교하면 `render/guide.js` 를 통째로
지워도 통과한다.

여기서 잡으려는 회귀는 전부 "브라우저에서는 멀쩡해 보이는" 것들이다.

* 등급 문구 셋 중 둘이 같아지는 것 — 화면은 정상으로 보이고, 사용자만 구분하지 못한다.
* 출처 없는 설명이 렌더링되는 것 — 문장은 그대로 보이고, 저작자 표시만 사라진다(CC BY-SA 4.0).
* 빈 설명이 **빈칸**으로 나가는 것 — "로딩 중"으로도, "그런 것이 없는 장소"로도 읽힌다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB = PROJECT_ROOT / "src" / "harbor_lantern" / "web"
GUIDE_JS = WEB / "js" / "render" / "guide.js"
EXPLORE_JS = WEB / "js" / "explore.js"
INDEX_HTML = WEB / "index.html"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node 실행기가 없다 — 가이드 화면의 순수 함수는 수동 확인 대상이 된다",
)

# 굽기가 끝나지 않아도 돌아야 한다 — **고정 픽스처**다. `seed/city-guides/` 를 읽지 않는다.
SPOT_FULL = {
    "id": "wd:Q243",
    "name": "에펠탑",
    "name_original": "Tour Eiffel",
    "category": "monument",
    "area": "7구",
    "description": "파리 샹드마르스 공원에 있는 철제 탑이다.",
    "description_source": {
        "url": "https://ko.wikipedia.org/wiki/에펠탑",
        "title": "에펠탑",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "retrieved_at": "2026-09-15",
    },
    "verification": {"status": "passed", "coord_delta_m": 12.4, "label_match": "exact", "reason": ""},
    "hours_text": "Mo-Su 09:00-23:45",
    "hours_source": {"url": "https://www.openstreetmap.org/node/5013364", "provider": "OpenStreetMap",
                     "license": "ODbL 1.0", "retrieved_at": "2026-09-15"},
    "tips": [{"text": "영업시간 09:00–23:45 (OpenStreetMap 기준 · 방문 전 확인)",
              "evidence": "opening_hours", "source_url": "https://www.openstreetmap.org/node/5013364"}],
    "recommendations": [],
}
SPOT_BARE = {
    "id": "wd:Q999",
    "name": "이름만 있는 곳",
    "name_original": "",
    "category": "park",
    "area": "",
    "description": "",
    "description_source": None,
    "verification": {"status": "failed", "reason": "no_description"},
    "hours_text": "",
    "hours_source": None,
    "tips": [],
    "recommendations": [],
}
# 설명은 있는데 출처 세 필드 중 라이선스가 없다 — **설명을 그리면 안 된다**(AC-070).
SPOT_UNSOURCED = {
    **SPOT_FULL,
    "id": "wd:Q1",
    "name": "출처가 모자란 곳",
    "description_source": {"url": "https://ko.wikipedia.org/wiki/x", "title": "x", "retrieved_at": "2026-09-15"},
}


def _run_node(script: str) -> dict:
    completed = subprocess.run(  # noqa: S603 — 인자는 전부 이 파일 안의 상수다
        [NODE, "--input-type=module"],
        input=script, capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=PROJECT_ROOT,
    )
    assert completed.returncode == 0, (
        f"node 실행이 실패했다 (exit {completed.returncode}):\n{completed.stderr[:2000]}"
    )
    tail = [line for line in completed.stdout.splitlines() if line.strip()]
    assert tail, f"node 가 아무것도 출력하지 않았다:\n{completed.stderr[:2000]}"
    return json.loads(tail[-1])


def _call(expression: str, **payload: object) -> dict:
    """`render/guide.js` 를 import 해 한 식을 평가하고 결과를 JSON 으로 받는다."""
    bindings = "\n".join(
        f"const {name} = {json.dumps(value, ensure_ascii=False)};" for name, value in payload.items()
    )
    return _run_node(
        f"import * as g from {GUIDE_JS.as_uri()!r};\n"
        f"{bindings}\n"
        f"console.log(JSON.stringify({{value: {expression}}}));\n"
    )


def test_node_is_running_the_shipped_file() -> None:
    """검사기 자신에 대한 검사 — 엉뚱한 파일을 돌리고 통과하는 일이 없게."""
    assert GUIDE_JS.is_file()
    result = _run_node(
        f"import * as m from {GUIDE_JS.as_uri()!r};\n"
        "console.log(JSON.stringify({exports: Object.keys(m).sort()}));\n"
    )
    for name in ("gradeNotice", "planGrade", "descriptionHtml", "tipsHtml", "daysHtml", "sourcesHtml"):
        assert name in result["exports"], f"{name} 를 export 하지 않는다: {result['exports']}"


# ── AC-085 : 등급 세 문구가 서로 다르다 ───────────────────────────────────
def test_three_grade_notices_differ() -> None:
    notices = _call("['full','partial','heuristic'].map(x => g.gradeNotice(x, '2026-09-15'))")["value"]
    assert len(set(notices)) == 3, f"등급 문구가 겹친다 (AC-085): {notices}"
    assert all(notice.strip() for notice in notices), "빈 등급 문구가 있다"


def test_grade_notices_say_what_the_grade_means() -> None:
    notices = dict(zip(
        ("full", "partial", "heuristic"),
        _call("['full','partial','heuristic'].map(x => g.gradeNotice(x, '2026-09-15'))")["value"],
        strict=True,
    ))
    assert "2026-09-15" in notices["full"], "완전 가이드에 조사 시점이 없다 (AC-080)"
    assert "2026-09-15" in notices["partial"]
    assert "일정 일부가 채워지지 않을 수 있습니다" in notices["partial"], "부분 가이드 안내가 없다 (AC-085)"
    assert "제한된 자동 추천" in notices["heuristic"], "폴백 표시가 없다 (AC-077 · AC-085)"


def test_missing_retrieved_at_is_written_not_left_blank() -> None:
    notice = _call("g.gradeNotice('full', '')")["value"]
    assert "{retrieved_at}" not in notice, "치환되지 않은 자리표시자가 화면에 나간다"
    assert "미제공" in notice, "조사 시점이 없을 때 빈칸으로 둔다"


def test_unknown_grade_is_read_down_not_up() -> None:
    """등급이 없는 일정(= 이 기능 전에 저장된 일정)을 "부분 가이드"라고 부르지 않는다."""
    grades = _call("[g.planGrade({}), g.planGrade({guide_grade:''}), g.planGrade({guide_grade:'gold'})]")["value"]
    assert grades == ["heuristic", "heuristic", "heuristic"], grades
    assert _call("g.planGrade({guide_grade:'full'})")["value"] == "full"


def test_grade_badge_is_present_for_the_fallback_plan_too() -> None:
    """폴백 일정에도 표시가 있다 (AC-077)."""
    html = _call(
        "g.gradeBadgeHtml(plan)",
        plan={"guide_grade": "heuristic", "guide_city": None,
              "guide_notice": "제한된 자동 추천 — 조사된 도시 가이드가 없습니다."},
    )["value"]
    assert "제한된 자동 추천" in html
    assert "gradebadge" in html


def test_server_notice_wins_when_present() -> None:
    """서버와 화면의 문구가 갈리지 않게 — 실려 온 문구가 있으면 그것을 쓴다."""
    value = _call("g.planNotice(plan)", plan={"guide_grade": "full", "guide_notice": "서버가 보낸 문구"})["value"]
    assert value == "서버가 보낸 문구"


# ── AC-070 · AC-080 : 설명에는 출처가 붙고, 없으면 "설명 미제공" ───────────
def test_description_renders_with_clickable_source_and_license() -> None:
    html = _call("g.descriptionHtml(spot)", spot=SPOT_FULL)["value"]
    assert "파리 샹드마르스 공원에 있는 철제 탑이다." in html
    assert 'href="https://ko.wikipedia.org/wiki/에펠탑"' in html, "출처 링크가 클릭 가능한 형태가 아니다 (AC-080)"
    assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html
    assert "CC BY-SA 4.0" in html, "라이선스 표시가 없다 (REQ-024)"
    assert "creativecommons.org/licenses/by-sa/4.0" in html, "라이선스 링크가 없다"
    assert "2026-09-15" in html, "조회일이 없다 (AC-070 의 세 필드)"


def test_missing_description_is_written_as_missing() -> None:
    html = _call("g.descriptionHtml(spot)", spot=SPOT_BARE)["value"]
    assert "설명 미제공" in html, "빈 설명이 빈칸으로 나간다 (AC-070)"


def test_description_without_full_source_is_not_rendered() -> None:
    """세 필드 중 하나라도 없으면 **문장 자체를 그리지 않는다** — 출처 없는 설명은 우리 글로 보인다."""
    html = _call("g.descriptionHtml(spot)", spot=SPOT_UNSOURCED)["value"]
    assert "설명 미제공" in html
    assert "철제 탑" not in html, "출처가 모자란 설명이 화면에 나갔다 (AC-070)"


def test_failed_verification_hides_the_description() -> None:
    spot = {**SPOT_FULL, "verification": {"status": "failed", "reason": "label_mismatch"}}
    html = _call("g.descriptionHtml(spot)", spot=spot)["value"]
    assert "설명 미제공" in html and "철제 탑" not in html, "연결 검증에 실패한 설명이 나갔다 (AC-070 · AC-088)"


# ── AC-072 : 없는 것은 "미제공". 기본 문구로 메우지 않는다 ────────────────
def test_empty_tips_and_recommendations_say_not_provided() -> None:
    result = _call("[g.tipsHtml(spot), g.recommendationsHtml(spot)]", spot=SPOT_BARE)["value"]
    assert "미제공" in result[0] and "미제공" in result[1]
    assert "<li>" not in result[0] and "<li>" not in result[1], "없는 항목을 그렸다"


def test_tip_without_evidence_is_dropped() -> None:
    spot = {**SPOT_BARE, "tips": [{"text": "근거 없는 팁"}]}
    html = _call("g.tipsHtml(spot)", spot=spot)["value"]
    assert "근거 없는 팁" not in html, "근거 필드가 없는 팁을 그렸다 (AC-072)"
    assert "미제공" in html


def test_tip_with_evidence_carries_its_source() -> None:
    html = _call("g.tipsHtml(spot)", spot=SPOT_FULL)["value"]
    assert "영업시간 09:00" in html
    assert "openstreetmap.org/node/5013364" in html, "팁의 근거 링크가 없다"


# ── AC-069 : 이름은 한국어 + 원어명. 그리고 이스케이프 ────────────────────
def test_spot_body_shows_korean_and_original_names() -> None:
    html = _call("g.spotBody(spot)", spot=SPOT_FULL)["value"]
    assert "에펠탑" in html and "Tour Eiffel" in html


def test_spot_name_is_escaped() -> None:
    spot = {**SPOT_BARE, "name": "<script>alert(1)</script>"}
    html = _call("g.spotBody(spot)", spot=spot)["value"]
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_day_color_is_rejected_unless_it_is_a_hex_color() -> None:
    values = _call("['#22d3ee','javascript:alert(1)','red;background:url(x)'].map(g.safeColor)")["value"]
    assert values == ["#22d3ee", "", ""], values


# ── 하루 카드 · 출처 패널 ─────────────────────────────────────────────────
def test_day_card_shows_theme_and_area_exception() -> None:
    day = {
        "date": "2026-10-01", "weekday": 3, "title": "센강 서쪽", "area": "7구", "color": "#22d3ee",
        "distance_m": 4200, "travel_minutes": 31, "area_exception": True,
        "area_exception_reason": "이 날에는 7구 밖의 스팟이 함께 배정됐습니다 — 루브르 박물관.",
        "stops": [{"place": SPOT_FULL, "arrival": "09:00", "departure": "10:30",
                   "travel_minutes": 0, "distance_m": 0, "hours_status": "weekly_hours", "evening_slot": False}],
    }
    html = _call("g.dayHtml(day, 0)", day=day)["value"]
    assert "DAY 1" in html and "센강 서쪽" in html and "7구" in html
    assert "다른 지역의 대표 장소가 포함" in html, "area_exception 을 화면이 말하지 않는다 (AC-073)"
    assert "루브르 박물관" in html
    assert "4.2km" in html and "31분" in html


def test_empty_day_says_the_schedule_was_not_filled() -> None:
    """부분 가이드의 빈 하루는 **비어 있다고 말한다** — 빈 카드로 두지 않는다(AC-085 · O18)."""
    day = {"date": "2026-10-02", "weekday": 4, "title": "", "area": "", "color": "",
           "distance_m": 0, "travel_minutes": 0, "stops": []}
    html = _call("g.dayHtml(day, 1)", day=day)["value"]
    assert "채워지지 않았습니다" in html


def test_sources_panel_is_always_attached_with_the_machine_made_line() -> None:
    guide_city = {"city_id": "paris", "retrieved_at": "2026-09-15",
                  "sources": [{"what": "위키데이터", "url": "https://query.wikidata.org/"}],
                  "known_gaps": ["설명이 없는 스팟 3곳"]}
    html = _call("g.sourcesHtml(city)", city=guide_city)["value"]
    assert "<details" in html and "출처와 한계" in html
    assert "사람이 고르고 쓴 홍콩 가이드와는" in html, "자동 구성이라는 사실을 말하지 않는다 (REQ-029)"
    assert "설명이 없는 스팟 3곳" in html
    assert 'href="https://query.wikidata.org/"' in html


def test_sources_panel_survives_an_empty_guide() -> None:
    """굽기 전에도 화면이 죽지 않아야 한다 — 지금이 그 상태다."""
    html = _call("g.sourcesHtml(city)", city={})["value"]
    assert "<details" in html and "미제공" in html


# ── AC-064 : 개수는 등급별로만 말한다 ─────────────────────────────────────
def test_counts_are_split_by_grade() -> None:
    value = _call("g.gradeSummary(counts)", counts={"surveyed": 30, "exported": 18, "full": 11, "partial": 7})["value"]
    assert value == "완전 11 · 부분 7", value
    assert _call("g.gradeSummary({})")["value"] == ""


def test_city_label_carries_the_grade() -> None:
    label = _call("g.cityLabel(city)", city={"city_id": "paris", "name_ko": "파리", "country_ko": "프랑스",
                                             "grade": "partial", "spot_count": 12})["value"]
    assert "파리" in label and "프랑스" in label and "부분 가이드" in label and "12곳" in label


# ── 정적 검사 : 금칙 문구 · 배선 ──────────────────────────────────────────
FORBIDDEN_FILLERS = ("꼭 방문해 보세요", "놓치지 마세요", "강력 추천", "여행의 필수 코스")


def _web_sources() -> list[Path]:
    files = [INDEX_HTML, WEB / "css" / "explore.css"]
    files += sorted((WEB / "js").rglob("*.js"))
    return [path for path in files if path.is_file()]


def test_no_filler_phrases_in_the_frontend() -> None:
    offenders = [
        f"{path.relative_to(PROJECT_ROOT)} → {phrase}"
        for path in _web_sources()
        for phrase in FORBIDDEN_FILLERS
        if phrase in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "빈자리를 메우는 기본 문구가 화면에 있다 (AC-072):\n  " + "\n  ".join(offenders)


def test_no_blanket_city_count_claim() -> None:
    """"도시 N개 지원" 류의 일괄 주장을 하지 않는다 (AC-064)."""
    pattern = re.compile(r"도시\s*\d+\s*개\s*(지원|제공|수록)")
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _web_sources()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"등급과 무관한 일괄 주장이 있다 (AC-064): {offenders}"


def test_explore_page_wires_the_guide_renderer() -> None:
    source = EXPLORE_JS.read_text(encoding="utf-8")
    assert "./render/guide.js" in source, "explore.js 가 가이드 렌더러를 쓰지 않는다"
    assert "guides?q=" in source, "구운 도시 목록을 조회하지 않는다 (REQ-027)"
    assert "city_id" in source, "city_id 를 일정 요청에 싣지 않는다 (DSN-45)"


def test_every_element_id_used_by_explore_js_exists_in_the_page() -> None:
    """번들러가 없으므로 오타는 브라우저에서 `null.textContent` 로만 드러난다 — 여기서 잡는다."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    ids = set(re.findall(r"""\$\('([A-Za-z0-9_]+)'\)""", EXPLORE_JS.read_text(encoding="utf-8")))
    assert ids, "explore.js 가 아무 요소도 찾지 않는다 — 검사가 무의미해진다"
    present = set(re.findall(r"""\bid=["']([A-Za-z0-9_]+)["']""", html))
    missing = sorted(ids - present)
    assert not missing, f"index.html 에 없는 요소를 찾는다: {missing}"


def test_hongkong_page_is_untouched_by_this_change() -> None:
    """홍콩 공유 일정은 이 확장의 대상이 아니다 (설계서 §16.20)."""
    hongkong = (WEB / "hongkong.html").read_text(encoding="utf-8")
    assert "./js/main.js" in hongkong and "guide.js" not in hongkong


# ── 서버 응답 ↔ 렌더러 계약 ───────────────────────────────────────────────
# 서버(T9)와 화면(T10)을 나눠 만들었으므로 **필드 이름이 갈리는 것**이 이 분업의 고유 위험이다.
# 그것은 서버 테스트에도, 렌더러 테스트에도 안 보인다 — 양쪽 다 자기 픽스처로 통과한다.
# 그래서 여기서 **실제 응답을 실제 렌더러에 먹인다.** 픽스처는 이 파일 안에 고정한다
# (`seed/city-guides/` 는 굽기 결과라 언제 무엇이 있는지 알 수 없다).
FIXTURE_SPOTS = [
    ("Q243", "에펠탑", "Tour Eiffel", 48.8584, 2.2945, "monument", 191, "7구"),
    ("Q19675", "루브르 박물관", "Musée du Louvre", 48.8606, 2.3376, "museum", 169, "1구"),
    ("Q2981", "노트르담 대성당", "Notre-Dame de Paris", 48.8530, 2.3499, "worship", 125, "4구"),
    ("Q83125", "개선문", "Arc de triomphe", 48.8738, 2.2950, "monument", 98, "8구"),
]


def _fixture_city() -> dict:
    spots = []
    for qid, name, original, lat, lng, category, sitelinks, area in FIXTURE_SPOTS:
        described = sitelinks > 100  # 일부만 설명이 있다 — "미제공"이 그려지는지 같이 본다
        spots.append({
            "id": f"wd:{qid}", "wikidata_id": qid, "name": name, "name_source": "ko",
            "name_original": original, "lat": lat, "lng": lng, "category": category,
            "wikidata_classes": [], "importance": {"sitelinks": sitelinks}, "area": area,
            "description": f"{name} 은(는) 파리의 대표 장소다." if described else "",
            "description_source": ({
                "url": f"https://ko.wikipedia.org/wiki/{name}", "title": name,
                "license": "CC BY-SA 4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "retrieved_at": "2026-09-15",
            } if described else None),
            "verification": {"status": "passed", "coord_delta_m": 10.0, "label_match": "exact",
                             "redirected": False, "reason": ""},
            "hours_text": "", "hours_source": None, "evening_candidate": False,
            "tips": [], "recommendations": [],
        })
    return {
        "schema_version": 1, "city_id": "paris", "name_ko": "파리", "name_local": "Paris",
        "name_en": "Paris", "country_code": "FR", "country_ko": "프랑스", "country_en": "France",
        "center": {"lat": 48.8566, "lng": 2.3522}, "radius_m": 6000, "retrieved_at": "2026-09-15",
        "grade": "full", "query": {"sitelink_min": 25, "limit": 150, "radius_m": 6000},
        "harvest": {"spot_count": len(spots), "grade_window": len(spots)},
        "sources": [{"what": "Wikidata", "url": "https://query.wikidata.org/", "retrieved_at": "2026-09-15"}],
        "known_gaps": ["설명이 없는 스팟이 있다 — 한국어 문서가 없다."],
        "spots": spots,
    }


@pytest.fixture
def baked_fixture(tmp_path: Path):
    from harbor_lantern.services import guides

    directory = tmp_path / "city-guides"
    directory.mkdir()
    city = _fixture_city()
    index = {
        "dataset": "city-guides", "retrieved_at": "2026-09-15", "what_this_is": "테스트 픽스처",
        "sources": city["sources"], "known_gaps": ["한 도시뿐이다."],
        "counts": {"surveyed": 2, "exported": 1, "full": 1, "partial": 0},
        "cities": [{key: city[key] for key in (
            "city_id", "name_ko", "name_local", "name_en", "country_code", "country_ko",
            "country_en", "center", "grade", "retrieved_at")} | {"spot_count": len(city["spots"])}],
    }
    for name, document in (("paris", city), ("index", index)):
        (directory / f"{name}.json").write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
            encoding="utf-8", newline="\n",
        )
    guides.load_index.cache_clear()
    guides.load_city.cache_clear()
    yield directory
    guides.load_index.cache_clear()
    guides.load_city.cache_clear()


def test_the_real_plan_response_renders_with_descriptions_and_sources(app, client, baked_fixture) -> None:
    """`POST /api/explore/plan` 의 실제 응답을 `render/guide.js` 에 그대로 먹인다."""
    app.state.guides_dir = str(baked_fixture)
    response = client.post("/api/explore/plan", json={
        "city_id": "paris", "start_date": "2026-10-01", "end_date": "2026-10-02",
        "pace": "balanced", "interests": "mixed",
    })
    assert response.status_code == 200, response.text
    plan = response.json()

    html = _call("g.daysHtml(plan) + g.gradeBadgeHtml(plan) + g.sourcesHtml(plan.guide_city)", plan=plan)["value"]
    assert "에펠탑" in html and "Tour Eiffel" in html, "스팟 이름이 화면에 없다 (AC-069)"
    assert "파리의 대표 장소다" in html, "설명이 화면에 없다 (REQ-024)"
    assert 'href="https://ko.wikipedia.org/wiki/에펠탑"' in html, "출처 링크가 없다 (AC-080)"
    assert "CC BY-SA 4.0" in html, "라이선스 표시가 없다"
    assert "설명 미제공" in html, "설명 없는 스팟이 빈칸으로 나갔다 (AC-070)"
    assert "완전 가이드" in html, "등급 표시가 없다 (AC-085)"
    assert "방문 팁 미제공" in html and "추천 미제공" in html, "빈 팁·추천이 빈칸으로 나갔다 (AC-072)"
    assert "사람이 고르고 쓴 홍콩 가이드와는" in html, "자동 구성이라는 사실이 없다 (REQ-029)"


def test_the_guide_list_response_renders_without_any_baked_city(app, client, tmp_path) -> None:
    """굽기 전(= 오늘)의 저장소에서도 목록 화면이 정상이다 — 빈 목록은 오류가 아니다(AC-076)."""
    from harbor_lantern.services import guides

    guides.load_index.cache_clear()
    guides.load_city.cache_clear()
    app.state.guides_dir = str(tmp_path / "없는디렉터리")
    body = client.get("/api/explore/guides", params={"q": "일본"}).json()
    guides.load_index.cache_clear()

    assert body["cities"] == []
    assert body["notice"], "빈 목록에도 안내 문구가 있어야 한다"
    assert _call("g.gradeSummary(counts)", counts=body["counts"])["value"] == ""
