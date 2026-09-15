"""큐레이션 지도 핀 묶기 — REQ-019 (AC-062).

`web/js/map.js` 의 `groupByCoordinate()`/`pinLabel()` 을 **실제 배포되는 파일 그대로**
Node 로 돌린다. 파이썬으로 같은 식을 다시 짜 비교하면 `map.js` 를 통째로 지워도 통과한다.

**왜 이 로직이 따로 검증돼야 하는가.** 큐레이션 좌표의 상당수가 건물 단위다 — 랜드마크
한 곳에만 미쉐린 6곳이 있다. 묶지 않고 그대로 찍으면 핀 6개가 완전히 겹쳐 **하나처럼
보이고 맨 위 하나만 눌린다.** 사용자는 나머지 5곳이 지도에 없다고 믿는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAP_JS = PROJECT_ROOT / "src" / "harbor_lantern" / "web" / "js" / "map.js"
SEED = PROJECT_ROOT / "seed" / "curated-places.json"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None, reason="node 실행기가 없다 — 지도 핀 묶기는 수동 확인 대상이 된다",
)


def _run(body: str) -> object:
    script = f"import {{ groupByCoordinate, pinLabel }} from {MAP_JS.as_uri()!r};\n{body}"
    done = subprocess.run(  # noqa: S603 — 인자는 이 파일 안의 상수뿐이다
        [NODE, "--input-type=module"], input=script, capture_output=True,
        text=True, encoding="utf-8", timeout=60, cwd=PROJECT_ROOT,
    )
    assert done.returncode == 0, f"node 실행 실패:\n{done.stderr[:1500]}"
    lines = [ln for ln in done.stdout.splitlines() if ln.strip()]
    assert lines, f"출력이 없다:\n{done.stderr[:1500]}"
    return json.loads(lines[-1])


def test_places_sharing_a_building_collapse_into_one_pin() -> None:
    result = _run("""
const places = [
  { name: 'A', lat: 22.28082, lng: 114.15772, stars: 3 },
  { name: 'B', lat: 22.28082, lng: 114.15772, stars: 2 },
  { name: 'C', lat: 22.30000, lng: 114.17000, stars: 1 },
];
const groups = groupByCoordinate(places);
console.log(JSON.stringify(groups.map(g => ({ n: g.items.length, label: pinLabel(g) }))));
""")
    assert result == [{"n": 2, "label": "2"}, {"n": 1, "label": "★"}]


def test_entries_without_coordinates_never_reach_the_map() -> None:
    """좌표가 없으면 지도에 올릴 방법이 없다 — 조용히 0,0 에 찍히면 안 된다."""
    result = _run("""
const groups = groupByCoordinate([
  { name: 'no coords', lat: null, lng: null, stars: 1 },
  { name: 'ok', lat: 22.3, lng: 114.17, stars: 1 },
  null,
]);
console.log(JSON.stringify({ groups: groups.length, names: groups.map(g => g.items[0].name) }));
""")
    assert result == {"groups": 1, "names": ["ok"]}


def test_single_place_pin_shows_its_star_count() -> None:
    result = _run("""
const label = (stars) => pinLabel({ items: [{ stars }] });
console.log(JSON.stringify([label(1), label(2), label(3)]));
""")
    assert result == ["★", "★★", "★★★"]


def test_the_real_dataset_collapses_to_fewer_pins_than_places() -> None:
    """**실제 데이터로** 확인한다 — 합성 예제만 통과하는 로직일 수 있다."""
    document = json.loads(SEED.read_text(encoding="utf-8"))
    located = [p for p in document["places"] if p.get("lat") is not None]
    result = _run(f"""
const places = {json.dumps(located, ensure_ascii=False)};
const groups = groupByCoordinate(places);
const biggest = Math.max(...groups.map(g => g.items.length));
console.log(JSON.stringify({{
  places: places.length, pins: groups.length, biggest,
  covered: groups.reduce((n, g) => n + g.items.length, 0),
}}));
""")
    assert result["covered"] == len(located), "묶는 과정에서 항목이 사라졌다"
    assert result["pins"] < result["places"], "겹치는 좌표가 있는데 하나도 묶이지 않았다"
    assert result["biggest"] >= 2
