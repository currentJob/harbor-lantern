"""프론트 순수 함수 실행 검증 — AC-031 · AC-032(클라이언트 측) · AC-033(클라이언트 측).

Phase 4 의 AC 대조에서 **구멍 하나가 드러났다**: `tests/static/test_frontend_assets.py` 는
프론트를 *텍스트로만* 본다(외부 도메인·CSS 선언·import 경로). 그래서 `web/js/format.js` 의
`formatHkt()` 와 `web/js/geo.js` 의 `sortByDistance()` 는 **한 번도 실행된 적이 없었다.**

- AC-031(홍콩 현지시각 `HH:MM`)은 서버에 대응 함수가 없다. 시계는 클라이언트 기능이고
  설계서 §2.3 이 그 이유로 `formatHkt(utcMillis)` 를 **순수 함수로 분리**해 뒀다.
  그 함수를 실행하지 않으면 AC-031 은 "주석으로만 커버된 AC" 다.
- AC-032 후단(거리 표기)·AC-033 후단(위치가 없으면 정렬 요청을 무시하고 기존 순서 유지)도
  같다 — 서버 쪽(`domain/geo.py`)은 `tests/domain/test_geo.py` 가 덮지만, 설계서 §6.3 이
  "두 구현이 갈라지지 않게 양쪽에서 검증한다"고 적어 둔 **클라이언트 쪽 절반**이 비어 있었다.

그래서 **실제로 배포되는 그 파일을** Node 로 import 해서 돌린다. 파이썬으로 같은 식을 다시
구현해 비교하는 것은 검증이 아니라 베끼기다 — 그렇게 하면 `format.js` 를 통째로 지워도 통과한다.

**Node 는 런타임 의존성이 아니다.** NFR-001 이 금지하는 것은 *배포되는 프론트*의 빌드 체인
(번들러·트랜스파일러·npm 런타임)이고, 여기서 Node 는 브라우저 대역의 **테스트 실행기**로만 쓴다.
`node_modules` 도 `package.json` 도 만들지 않는다(그것이 있으면 AC-042 가 실패한다).
Node 가 없는 기계에서는 skip 되며, 그 사실은 `docs/04_테스트결과서.md` 에 적혀 있다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_JS = PROJECT_ROOT / "src" / "harbor_lantern" / "web" / "js"
FORMAT_JS = WEB_JS / "format.js"
GEO_JS = WEB_JS / "geo.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node 실행기가 없다 — 프론트 순수 함수(AC-031·AC-032·AC-033 클라이언트 측)는 수동 확인 대상이 된다",
)


def _run_node(script: str) -> dict:
    """ES 모듈 조각을 돌리고 `console.log` 한 줄 JSON 을 돌려받는다.

    `--input-type=module` 로 stdin 을 모듈로 읽힌다 — 임시 파일을 만들면 상대 import 가
    저장소 밖을 가리키게 되므로 import 는 **파일 URL 절대 경로**로 건다.
    """
    completed = subprocess.run(  # noqa: S603 — 인자는 전부 이 파일 안의 상수다
        [NODE, "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        cwd=PROJECT_ROOT,
    )
    assert completed.returncode == 0, (
        f"node 실행이 실패했다 (exit {completed.returncode}):\n{completed.stderr[:2000]}"
    )
    tail = [line for line in completed.stdout.splitlines() if line.strip()]
    assert tail, f"node 가 아무것도 출력하지 않았다:\n{completed.stderr[:2000]}"
    return json.loads(tail[-1])


def test_node_is_actually_running_the_shipped_file() -> None:
    """검사기 자신에 대한 검사 — 엉뚱한 파일을 돌리고 통과하는 일이 없게."""
    assert FORMAT_JS.is_file() and GEO_JS.is_file()
    result = _run_node(
        f"import * as m from {FORMAT_JS.as_uri()!r};\n"
        "console.log(JSON.stringify({exports: Object.keys(m).sort()}));\n"
    )
    for name in ("formatHkt", "formatDistance", "HKT_OFFSET_MINUTES"):
        assert name in result["exports"], f"{name} 를 export 하지 않는다: {result['exports']}"


# ── AC-031 : 고정 UTC 주입 → 홍콩 현지시각 24시간제 HH:MM ────────────────────
# 계산 근거는 UTC+8 고정(가정 A9)이다. 기대값은 구현을 본 것이 아니라 손으로 더한 것이다.
AC031_VECTORS = [
    # (UTC ISO, UTC epoch ms, 기대 HKT HH:MM)
    ("2026-10-05T02:00:00Z", 1_791_165_600_000, "10:00"),  # 02:00 UTC + 8h
    ("2026-10-05T00:00:00Z", 1_791_158_400_000, "08:00"),
    ("2026-10-04T16:00:00Z", 1_791_129_600_000, "00:00"),  # 자정 경계 (HKT 로 날이 바뀐다)
    ("2026-10-04T15:59:00Z", 1_791_129_540_000, "23:59"),  # 자정 직전
    ("2026-10-05T11:59:00Z", 1_791_201_540_000, "19:59"),
    ("2026-10-05T12:00:00Z", 1_791_201_600_000, "20:00"),  # 심포니 오브 라이트 시작 시각
    ("2026-01-15T03:05:00Z", 1_768_446_300_000, "11:05"),  # 겨울 — DST 가 없다는 사실 자체를 고정
    ("2026-07-15T03:05:00Z", 1_784_084_700_000, "11:05"),  # 여름 — 같은 오프셋이어야 한다
]


def test_ac031_vectors_are_the_utc_instants_we_claim() -> None:
    """테스트 벡터 자신의 검산 — epoch ms 가 정말 그 UTC 시각인가.

    이것을 안 하면 벡터가 틀렸을 때 `formatHkt` 가 범인이 된다.
    """
    from datetime import UTC, datetime

    for iso, millis, _ in AC031_VECTORS:
        expected = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        assert datetime.fromtimestamp(millis / 1000, tz=UTC) == expected, iso


def test_ac031_format_hkt_returns_hong_kong_local_time() -> None:
    """AC-031: 고정된 UTC 시각을 주입하면 `Asia/Hong_Kong`(UTC+8) 24시간제 `HH:MM` 이 나온다."""
    inputs = [millis for _, millis, _ in AC031_VECTORS]
    result = _run_node(
        f"import {{ formatHkt }} from {FORMAT_JS.as_uri()!r};\n"
        f"const xs = {json.dumps(inputs)};\n"
        "console.log(JSON.stringify({out: xs.map(formatHkt)}));\n"
    )
    assert result["out"] == [expected for _, _, expected in AC031_VECTORS]


def test_ac031_offset_is_a_fixed_plus_eight_with_no_dst() -> None:
    """AC-031 근거: 오프셋 상수가 480분이고, 같은 시각을 두 번 물어도 같은 답이다."""
    result = _run_node(
        f"import {{ formatHkt, HKT_OFFSET_MINUTES }} from {FORMAT_JS.as_uri()!r};\n"
        "const t = 1791165600000;\n"
        "console.log(JSON.stringify({\n"
        "  offset: HKT_OFFSET_MINUTES,\n"
        "  repeat: [formatHkt(t), formatHkt(t)],\n"
        "  plusHour: formatHkt(t + 3600000),\n"
        "  minusDay: formatHkt(t - 86400000),\n"
        "}));\n"
    )
    assert result["offset"] == 480
    assert result["repeat"] == ["10:00", "10:00"], "같은 입력에 다른 답이 나왔다"
    assert result["plusHour"] == "11:00"
    assert result["minusDay"] == "10:00", "24시간 전은 같은 시각이어야 한다 (DST 없음 — 가정 A9)"


def test_ac031_output_is_always_two_digit_24_hour_form() -> None:
    """AC-031 형식: 0시·9시도 `00:00`·`09:05` 로 0 을 채운다(12시간제·오전/오후 표기 금지)."""
    result = _run_node(
        f"import {{ formatHkt }} from {FORMAT_JS.as_uri()!r};\n"
        # 2026-10-04T16:00:00Z 부터 24시간을 5분 간격으로 훑는다 — 288개 전부 검사한다.
        "const base = 1791129600000;\n"
        "const out = [];\n"
        "for (let i = 0; i < 288; i += 1) out.push(formatHkt(base + i * 300000));\n"
        "console.log(JSON.stringify({out}));\n"
    )
    stamps = result["out"]
    assert len(stamps) == 288
    assert stamps[0] == "00:00" and stamps[-1] == "23:55"
    for index, stamp in enumerate(stamps):
        assert len(stamp) == 5 and stamp[2] == ":", stamp
        hour, minute = int(stamp[:2]), int(stamp[3:])
        assert 0 <= hour <= 23 and 0 <= minute <= 59, stamp
        assert hour * 60 + minute == index * 5, f"{index}번째가 {stamp} 다 — 5분 간격이 깨졌다"


# ── AC-032 (클라이언트 측) : 거리 표기가 서버와 같은 규칙인가 ────────────────
AC032_CASES = [
    (0.0, "0m"),
    (4.0, "0m"),
    (5.0, "10m"),
    (12.0, "10m"),
    (128.0, "130m"),
    (994.0, "990m"),
    (995.0, "1000m"),
    (999.9, "1000m"),
    (1000.0, "1.0km"),
    (1249.0, "1.2km"),
    (1250.0, "1.3km"),
    (9999.0, "10.0km"),
    (10000.0, "10km"),
    (10500.0, "11km"),
    (12345.0, "12km"),
]


def test_ac032_client_distance_format_matches_the_server() -> None:
    """AC-032: 클라이언트 `formatDistance` 가 `domain/geo.py` 와 **같은 문자열**을 낸다.

    설계서 §6.3 — 현재 위치 기반 거리는 클라이언트가 계산하므로 표기 규칙이 두 벌 존재한다.
    한쪽만 고치면 화면과 API 가 조용히 어긋난다(`990m` vs `1.0km`).
    """
    from harbor_lantern.domain.geo import format_distance

    result = _run_node(
        f"import {{ formatDistance }} from {FORMAT_JS.as_uri()!r};\n"
        f"const xs = {json.dumps([meters for meters, _ in AC032_CASES])};\n"
        "console.log(JSON.stringify({out: xs.map(formatDistance)}));\n"
    )
    expected = [text for _, text in AC032_CASES]
    assert result["out"] == expected
    assert [format_distance(meters) for meters, _ in AC032_CASES] == expected, (
        "서버 구현이 같은 표에서 갈라졌다 (설계서 §6.3)"
    )


def test_ac032_client_haversine_agrees_with_the_server() -> None:
    """AC-032 전제: 거리 자체도 같은 공식·같은 반지름이어야 한다(오차 1% 이내 — AC-019 규약)."""
    from harbor_lantern.domain.geo import haversine_m
    from harbor_lantern.domain.models import LatLng

    pairs = [
        ((22.2937, 114.1730), (22.2938, 114.1694)),  # 스타 애비뉴 ↔ 시계탑
        ((22.2937, 114.1730), (22.2759, 114.1455)),  # ↔ 빅토리아 피크
        ((22.2937, 114.1730), (22.2551, 113.8630)),  # ↔ 타이오
    ]
    result = _run_node(
        f"import {{ haversineMeters, EARTH_RADIUS_M }} from {GEO_JS.as_uri()!r};\n"
        f"const ps = {json.dumps(pairs)};\n"
        "console.log(JSON.stringify({\n"
        "  radius: EARTH_RADIUS_M,\n"
        "  out: ps.map(([a, b]) => haversineMeters({lat: a[0], lng: a[1]}, {lat: b[0], lng: b[1]})),\n"
        "}));\n"
    )
    assert result["radius"] == 6_371_000
    for (a, b), measured in zip(pairs, result["out"], strict=True):
        server = haversine_m(LatLng(*a), LatLng(*b))
        assert measured == pytest.approx(server, rel=0.01), f"{a}→{b}: js={measured} py={server}"


# ── AC-033 (클라이언트 측) : 가까운 순 정렬 · 위치 없으면 무시 ───────────────
def test_ac033_sort_by_distance_orders_ascending() -> None:
    """AC-033 전단: 현재 위치로부터 거리 오름차순으로 정렬된다."""
    result = _run_node(
        f"import {{ sortByDistance }} from {GEO_JS.as_uri()!r};\n"
        "const spots = [\n"
        "  {id: 'taio',  lat: 22.2551, lng: 113.8630},\n"
        "  {id: 'peak',  lat: 22.2759, lng: 114.1455},\n"
        "  {id: 'clock', lat: 22.2938, lng: 114.1694},\n"
        "];\n"
        "const me = {lat: 22.2940, lng: 114.1700};\n"
        "console.log(JSON.stringify({\n"
        "  sorted: sortByDistance(spots, me, (s) => s).map((s) => s.id),\n"
        "  original: spots.map((s) => s.id),\n"
        "}));\n"
    )
    assert result["sorted"] == ["clock", "peak", "taio"]
    assert result["original"] == ["taio", "peak", "clock"], "입력 배열을 제자리에서 뒤집었다"


def test_ac033_without_a_location_the_sort_request_is_ignored() -> None:
    """AC-033 후단: 위치가 없으면 **정렬 요청이 무시되고 기존 순서가 유지된다**(리스크 R5).

    위치 권한이 거부돼도 목록은 그대로 보여야 한다. 여기서 빈 배열이나 예외가 나오면
    권한을 거부한 사용자에게는 앱이 통째로 비어 보인다.
    """
    result = _run_node(
        f"import {{ sortByDistance }} from {GEO_JS.as_uri()!r};\n"
        "const spots = [{id: 'a', lat: 22.1, lng: 114.1}, {id: 'b', lat: 22.9, lng: 114.9}];\n"
        "const coord = (s) => s;\n"
        "console.log(JSON.stringify({\n"
        "  nullish: sortByDistance(spots, null, coord).map((s) => s.id),\n"
        "  undef: sortByDistance(spots, undefined, coord).map((s) => s.id),\n"
        "  copied: sortByDistance(spots, null, coord) !== spots,\n"
        "}));\n"
    )
    assert result["nullish"] == ["a", "b"]
    assert result["undef"] == ["a", "b"]
    assert result["copied"] is True, "원본 배열을 그대로 돌려주면 호출자가 뒤에서 망가뜨린다"


def test_ac033_equal_distances_keep_the_original_order() -> None:
    """AC-033 경계: 거리가 같으면 원래 순서 — 화면이 이유 없이 흔들리지 않는다."""
    result = _run_node(
        f"import {{ sortByDistance }} from {GEO_JS.as_uri()!r};\n"
        "const same = [{id: 'a', lat: 22.2938, lng: 114.1694}, {id: 'b', lat: 22.2938, lng: 114.1694}];\n"
        "console.log(JSON.stringify({\n"
        "  out: sortByDistance(same, {lat: 22.30, lng: 114.17}, (s) => s).map((s) => s.id),\n"
        "}));\n"
    )
    assert result["out"] == ["a", "b"]


def test_ac034_client_directions_url_matches_the_server() -> None:
    """AC-034: 길찾기 딥링크도 두 구현이 같은 문자열을 만든다(서버는 `spot.directions_url`)."""
    from harbor_lantern.domain.geo import directions_url

    coords = [(22.2937, 114.1730), (22.2551, 113.8630), (22.3193, 114.1694)]
    result = _run_node(
        f"import {{ directionsUrl }} from {GEO_JS.as_uri()!r};\n"
        f"const cs = {json.dumps(coords)};\n"
        "console.log(JSON.stringify({out: cs.map(([a, b]) => directionsUrl(a, b))}));\n"
    )
    assert result["out"] == [directions_url(lat, lng) for lat, lng in coords]
    assert all(url.startswith("https://www.google.com/maps/dir/?api=1&destination=") for url in result["out"])


def test_node_runner_reports_the_version_it_used(capsys: pytest.CaptureFixture[str]) -> None:
    """리포트에 적을 실행기 버전을 관측값으로 남긴다 — 환경을 안 적은 결과는 재현할 수 없다."""
    version = subprocess.run(  # noqa: S603
        [NODE, "--version"], capture_output=True, text=True, timeout=30
    ).stdout.strip()
    assert version.startswith("v")
    print(f"node runner: {version} (python {sys.version.split()[0]})")
