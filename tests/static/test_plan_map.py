"""설계한 일정의 지도 — 어댑터 실행 검증 (REQ-024 · REQ-028 화면 확장).

이 확장의 고유 위험은 **데이터 모양 하나**다. 지도(`js/map.js` 의 `TripMap`)는 홍콩
`/state` 의 `days[].spots[]` 를 기대하는데, 여행 설계 응답은 `days[].stops[].place` 다.
그 사이를 `js/render/planmap.js` 가 잇는다 — 어긋나면 **화면은 멀쩡하고 핀만 사라진다**.
브라우저에서는 "이 도시는 원래 핀이 적나 보다"로 보이고 아무 오류도 나지 않는다.

그래서 `test_guide_frontend.py` 와 같은 방식으로 **실제로 배포되는 그 파일을** Node 로
돌리고, **실제 `POST /api/explore/plan` 응답**을 그대로 먹인다. 픽스처를 새로 적으면
서버와 화면이 갈라지는 바로 그 순간이 안 보인다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# 실제 응답으로 검사하기 위해 구운 도시 픽스처를 그대로 쓴다 — 같은 것을 두 벌 적으면
# 둘이 갈라지고, 갈라진 쪽이 무엇을 검사하는지 아무도 모르게 된다.
from tests.static.test_guide_frontend import baked_fixture  # noqa: F401  (pytest fixture)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB = PROJECT_ROOT / "src" / "harbor_lantern" / "web"
PLANMAP_JS = WEB / "js" / "render" / "planmap.js"
GUIDE_JS = WEB / "js" / "render" / "guide.js"
MAP_JS = WEB / "js" / "map.js"
EXPLORE_JS = WEB / "js" / "explore.js"
INDEX_HTML = WEB / "index.html"
HONGKONG_HTML = WEB / "hongkong.html"

NODE = shutil.which("node")

node_only = pytest.mark.skipif(
    NODE is None,
    reason="node 실행기가 없다 — 지도 어댑터는 수동 확인 대상이 된다",
)


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
    bindings = "\n".join(
        f"const {name} = {json.dumps(value, ensure_ascii=False)};" for name, value in payload.items()
    )
    return _run_node(
        f"import * as m from {PLANMAP_JS.as_uri()!r};\n"
        f"import * as g from {GUIDE_JS.as_uri()!r};\n"
        f"{bindings}\n"
        f"console.log(JSON.stringify({{value: {expression}}}));\n"
    )


@node_only
def test_node_is_running_the_shipped_adapter() -> None:
    """검사기 자신에 대한 검사 — 엉뚱한 파일을 돌리고 통과하는 일이 없게."""
    assert PLANMAP_JS.is_file()
    exports = _run_node(
        f"import * as m from {PLANMAP_JS.as_uri()!r};\n"
        "console.log(JSON.stringify({exports: Object.keys(m).sort()}));\n"
    )["exports"]
    for name in ("planMapDays", "planMapSpotCount", "spotFromStop", "dayColor", "unmappedCount"):
        assert name in exports, f"{name} 를 export 하지 않는다: {exports}"


# ── TripMap 이 실제로 읽는 필드들 ─────────────────────────────────────────
# `map.js` 의 render()/focus()/focusDay() 가 건드리는 이름을 여기 고정한다.
# 이 목록이 map.js 와 갈라지면 아래 test_map_js_contract_is_unchanged 가 먼저 운다.
SPOT_FIELDS = ("id", "lat", "lng", "name", "name_original", "time_label", "hours_text")
DAY_FIELDS = ("day_index", "color", "title", "spots")


@node_only
def test_map_js_contract_is_unchanged() -> None:
    """지도 쪽 시그니처를 바꾸지 않았는지 — 바꿨다면 홍콩 화면이 같이 움직인 것이다."""
    source = MAP_JS.read_text(encoding="utf-8")
    for signature in ("render(days)", "focusDay(day)", "focus(spot, zoom = 15)", "showMe(me)", "flyToMe(me)"):
        assert signature in source, f"map.js 의 기존 시그니처가 바뀌었다: {signature}"
    # render() 가 읽는 필드가 어댑터가 만드는 필드와 같은가.
    for field in ("spot.lat", "spot.lng", "spot.name", "spot.time_label", "spot.hours_text", "spot.id", "day.color"):
        assert field in source, f"map.js 가 {field} 를 더는 읽지 않는다 — 어댑터를 같이 고쳐야 한다"


def _plan(client, baked) -> dict:
    response = client.post("/api/explore/plan", json={
        "city_id": "paris", "start_date": "2026-10-01", "end_date": "2026-10-03",
        "pace": "balanced", "interests": "mixed",
    })
    assert response.status_code == 200, response.text
    return response.json()


# ── 어댑터: 실제 응답 → 지도가 아는 모양 ──────────────────────────────────
@node_only
def test_the_real_plan_response_becomes_tripmap_days(app, client, baked_fixture) -> None:  # noqa: F811
    """`days[].stops[].place` 를 넣으면 `days[].spots[]` 가 나온다 — 이 확장의 핵심."""
    app.state.guides_dir = str(baked_fixture)
    plan = _plan(client, baked_fixture)

    days = _call("m.planMapDays(plan)", plan=plan)["value"]
    assert days, "일정이 있는데 지도 일자가 하나도 나오지 않았다"
    assert len(days) == len(plan["days"]), "일자 수가 응답과 다르다"
    for index, day in enumerate(days):
        for field in DAY_FIELDS:
            assert field in day, f"DAY {index + 1} 에 {field} 가 없다 (TripMap.render 가 읽는다)"
        assert day["day_index"] == index
        assert re.fullmatch(r"#[0-9a-fA-F]{3,8}", day["color"]), f"일자 색이 색이 아니다: {day['color']}"
        assert day["title"], "팝업 둘째 줄이 빌 자리다 — 일자 이름이 없다"
        for spot in day["spots"]:
            for field in SPOT_FIELDS:
                assert field in spot, f"스팟에 {field} 가 없다: {sorted(spot)}"
            assert isinstance(spot["lat"], (int, float)) and isinstance(spot["lng"], (int, float)), (
                f"좌표가 숫자가 아니다 — L.marker 가 받지 못한다: {spot}"
            )
            assert spot["name"], "핀 팝업의 이름이 비었다"
            assert spot["time_label"], "핀 팝업의 시각이 비었다"


@node_only
def test_every_scheduled_stop_gets_exactly_one_pin(app, client, baked_fixture) -> None:  # noqa: F811
    """목록에 있는데 핀이 없거나 그 반대면 안 된다 — 지도와 목록은 같은 데이터를 본다."""
    app.state.guides_dir = str(baked_fixture)
    plan = _plan(client, baked_fixture)
    stops = sum(len(day["stops"]) for day in plan["days"])
    assert stops == plan["scheduled_count"], "응답 자체가 어긋났다 — 이 테스트의 전제가 깨졌다"

    pins = _call("m.planMapSpotCount(m.planMapDays(plan))", plan=plan)["value"]
    assert pins == stops, f"핀 {pins}개 · 목록 {stops}곳 — 지도와 목록이 다른 것을 본다"
    assert _call("m.unmappedCount(plan)", plan=plan)["value"] == 0


@node_only
def test_pin_ids_are_the_dom_ids_of_the_rendered_list(app, client, baked_fixture) -> None:  # noqa: F811
    """핀을 눌러 목록으로 가는 끈 — 핀의 `id` 가 실제로 그려진 카드의 DOM id 여야 한다.

    이것이 어긋나면 클릭해도 **아무 일도 일어나지 않는다**(오류도 나지 않는다).
    """
    app.state.guides_dir = str(baked_fixture)
    plan = _plan(client, baked_fixture)

    result = _run_node(
        f"import * as m from {PLANMAP_JS.as_uri()!r};\n"
        f"import * as g from {GUIDE_JS.as_uri()!r};\n"
        f"const plan = {json.dumps(plan, ensure_ascii=False)};\n"
        "console.log(JSON.stringify({ids: m.planMapDays(plan).flatMap(d => d.spots.map(s => s.id)),"
        " html: g.daysHtml(plan)}));\n"
    )
    assert result["ids"], "핀이 하나도 없다 — 검사가 무의미해진다"
    rendered = set(re.findall(r"""<div class="stop" id="([^"]+)\"""", result["html"]))
    assert rendered, "목록 카드에 DOM id 가 없다 — 핀이 갈 자리가 없다"
    assert set(result["ids"]) == rendered, (
        f"핀 id 와 목록 id 가 다르다: 핀에만 {sorted(set(result['ids']) - rendered)} · "
        f"목록에만 {sorted(rendered - set(result['ids']))}"
    )
    assert len(result["ids"]) == len(set(result["ids"])), "핀 id 가 중복됐다 — 같은 카드로 두 핀이 간다"


# ── 폴백 일정(색·테마·hours_text 가 없는 쪽) ──────────────────────────────
FALLBACK_DAY = {
    "date": "2026-10-01", "weekday": 3, "distance_m": 1200, "travel_minutes": 18,
    "stops": [
        {"arrival": "10:00", "departure": "11:30", "travel_minutes": 0, "distance_m": 0,
         "place": {"name": "어느 식당", "lat": 34.6937, "lng": 135.5023, "category": "restaurant",
                   "opening_hours": "Mo-Su 11:00-22:00"}},
    ],
}


@node_only
def test_fallback_day_without_color_or_theme_still_gets_both() -> None:
    """폴백 일정에는 `color`·`title` 이 없다 — 없으면 핀이 검게 서고 팝업 한 줄이 빈다."""
    days = _call("m.planMapDays(plan)", plan={"days": [FALLBACK_DAY, FALLBACK_DAY]})["value"]
    assert days[0]["color"] == "#22d3ee" and days[1]["color"] == "#f472b6", (
        f"팔레트가 적용되지 않았다: {[day['color'] for day in days]}"
    )
    assert days[0]["title"] == "DAY 1" and days[1]["title"] == "DAY 2"
    # 영업시간 필드 이름이 두 경로에서 다르다: hours_text(가이드) · opening_hours(폴백).
    assert days[0]["spots"][0]["hours_text"] == "Mo-Su 11:00-22:00"
    assert days[0]["spots"][0]["time_label"] == "10:00–11:30"


@node_only
def test_a_stop_without_coordinates_is_skipped_and_counted() -> None:
    """좌표가 없으면 지도에 올릴 방법이 없다 — 0 으로 채우면 서아프리카 앞바다에 핀이 선다."""
    plan = {"days": [{"stops": [
        {"arrival": "10:00", "departure": "11:00", "place": {"name": "좌표 없음", "lat": None, "lng": None}},
        {"arrival": "12:00", "departure": "13:00", "place": {"name": "좌표 있음", "lat": 1.5, "lng": 2.5}},
    ]}]}
    days = _call("m.planMapDays(plan)", plan=plan)["value"]
    assert [spot["name"] for spot in days[0]["spots"]] == ["좌표 있음"]
    assert _call("m.unmappedCount(plan)", plan=plan)["value"] == 1, (
        "지도에 못 올린 수를 세지 않는다 — 화면이 그 사실을 말할 수 없다"
    )


@node_only
def test_empty_and_malformed_plans_do_not_throw() -> None:
    """빈 일정·없는 값이 정상 입력이다 — 여기서 던지면 목록까지 같이 죽는다."""
    for payload in ({}, {"days": []}, {"days": [{}]}, {"days": [{"stops": [{}]}]}):
        assert _call("m.planMapSpotCount(m.planMapDays(plan))", plan=payload)["value"] == 0


# ── 페이지 배선 ───────────────────────────────────────────────────────────
def test_index_html_loads_vendored_leaflet_and_has_the_map_container() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "./vendor/leaflet-1.9.4/leaflet.css" in html, "Leaflet CSS 가 없다"
    assert "./vendor/leaflet-1.9.4/leaflet.js" in html, "Leaflet JS 가 없다"
    for element_id in ("planMap", "planMapBlock", "planMapMsg", "planMapDay", "planMapLocate"):
        assert re.search(rf"""id=["']{element_id}["']""", html), f"#{element_id} 이 없다"
    # 일정 전에는 지도를 보이지 않는다 — 빈 지도는 "아직 로딩 중"으로도 읽힌다.
    assert re.search(r"""<div id=["']planMapBlock["'][^>]*\shidden""", html), (
        "planMapBlock 이 처음부터 보인다 — 일정이 없는 화면에 빈 지도가 뜬다"
    )


def test_index_html_has_no_external_script_or_link() -> None:
    """`index.html` 에도 홍콩 페이지와 같은 규칙을 적용한다 (AC-042)."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    refs = re.findall(r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", html)
    assert refs, "index.html 이 자산을 하나도 참조하지 않는다"
    external = [ref for ref in refs if ref.startswith(("http://", "https://", "//"))]
    assert not external, f"index.html 에 외부 자산 참조가 있다 (AC-042): {external}"
    for ref in refs:
        assert (WEB / ref.lstrip("./")).resolve().is_file(), f"index.html 이 없는 파일을 참조한다: {ref}"


def test_location_permission_is_only_requested_after_a_click() -> None:
    """권한을 먼저 묻지 않는다 — 기존 화면과 같은 규칙이다."""
    source = EXPLORE_JS.read_text(encoding="utf-8")
    handler = source.index("planMapLocate")
    for match in re.finditer(r"tracker\.start\(\)", source):
        assert match.start() > handler, "버튼 처리기 밖에서 위치 추적을 시작한다 — 권한을 먼저 묻게 된다"
    assert "getCurrentPosition" in source, "근처 맛집의 일회성 조회가 사라졌다 (기존 동작)"


def test_map_stays_optional_in_the_explore_screen() -> None:
    """지도를 못 세워도 목록은 그대로다 — `init()` 실패가 정상 경로여야 한다."""
    source = EXPLORE_JS.read_text(encoding="utf-8")
    assert "map.init() ? map : null" in source, "init() 실패를 정상 경로로 다루지 않는다"
    assert "onTileTrouble:mapNotice" in source, "타일 실패 경로가 화면에 연결되지 않았다"


def test_hongkong_page_does_not_learn_about_the_explore_map() -> None:
    """홍콩 화면은 이 확장의 대상이 아니다 — 지도는 공유하되 페이지는 건드리지 않는다."""
    hongkong = HONGKONG_HTML.read_text(encoding="utf-8")
    assert "planmap.js" not in hongkong and "planMap" not in hongkong
    assert "./js/main.js" in hongkong
