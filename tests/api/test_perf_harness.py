"""성능 측정 하네스 — AC-043 (NFR-010 · DSN-23 · 설계서 §8).

Phase 4 의 AC 대조에서 드러난 구멍: `tools/perf_report.py` 는 **손으로 돌릴 때만** 동작을
확인했고 스위트 안에서 한 번도 실행되지 않았다. 그 상태에서 하네스가 깨지면
(미들웨어의 `X-Process-Time` 이 빠지거나, 시드가 27건에서 벗어나거나, 퍼센타일 식이 뒤집혀도)
**릴리스 직전에 리포트를 만들려는 순간에야** 안다.

AC-043 이 요구하는 것은 두 가지다.
1. 27스팟 시드 여행에 대해 **전체 조회·재계산 요청의 p50/p95** 를 산출한다.
2. **리포트 파일을 남긴다.**

절대치는 실행 환경에 의존하므로(가정 A6 · 설계서 §8) **수치에는 단언하지 않는다** —
임계로 단언하면 러너가 느린 날 간헐적으로 빨개지고, 간헐적 실패는 아무도 안 본다.
대신 **구조와 불변식**(표본수·퍼센타일 단조성·환경 메타)을 단언한다.

표본은 5회로 줄인다. 하네스가 도는지를 보는 검사이지 성능을 재는 검사가 아니다 —
성능 수치는 `uv run python tools/perf_report.py` 가 200표본으로 따로 남긴다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HARNESS = PROJECT_ROOT / "tools" / "perf_report.py"
COMMITTED_JSON = PROJECT_ROOT / "reports" / "perf.json"
COMMITTED_MD = PROJECT_ROOT / "reports" / "perf.md"

REQUIRED_TARGET_FIELDS = {
    "name", "samples", "min_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "mean_ms",
    "target_p50_ms", "target_p95_ms", "within_target", "breach_limit_ms", "breached",
}


@pytest.fixture(scope="module")
def harness() -> Any:
    """`tools/` 는 패키지가 아니다 — 파일 경로로 직접 적재한다."""
    assert HARNESS.is_file(), "성능 측정 하네스가 없다 (AC-043 · DSN-23)"
    spec = importlib.util.spec_from_file_location("perf_report_under_test", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── AC-043 : 하네스가 실제로 돌고 p50/p95 를 산출한다 ──────────────────────
def test_ac043_harness_measures_the_27_spot_seed_trip(harness: Any) -> None:
    """AC-043: 27스팟 시드 여행에 대해 `/state`·`/optimize` 의 p50/p95 가 나온다."""
    report = harness.run(samples=5, warmup=1)

    assert report["spot_count"] == 27, "측정 대상이 27스팟 시드 여행이 아니다 (AC-043)"
    assert report["warmup"] == 1
    assert report["python"] and report["platform"], "환경을 안 적은 성능 수치는 비교할 수 없다"
    assert report["generated_at"].endswith("Z")

    names = [item["name"] for item in report["targets"]]
    assert any("/state" in name for name in names), f"전체 조회 종목이 없다: {names}"
    assert any("optimize" in name for name in names), f"재계산 종목이 없다: {names}"

    for item in report["targets"]:
        assert set(item) == REQUIRED_TARGET_FIELDS, f"{item['name']} 의 필드가 계약과 다르다"
        assert item["samples"] == 5
        # 수치 자체가 아니라 **정의상 반드시 성립해야 하는 순서**만 본다.
        assert item["min_ms"] <= item["p50_ms"] <= item["p95_ms"] <= item["p99_ms"] <= item["max_ms"], item
        assert item["p50_ms"] >= 0.0
        assert item["breach_limit_ms"] == harness.BREACH_LIMIT_MS


def test_ac043_percentile_is_nearest_rank_and_not_an_average(harness: Any) -> None:
    """AC-043 근거: p50/p95 가 **관측된 값**이어야 한다.

    퍼센타일 식이 평균으로 바뀌면 리포트는 계속 그럴듯한 숫자를 내고 아무도 눈치채지 못한다.
    """
    values = [float(n) for n in range(1, 101)]  # 1..100
    assert harness.percentile(values, 0.50) == 50.0
    assert harness.percentile(values, 0.95) == 95.0
    assert harness.percentile(values, 0.99) == 99.0
    assert harness.percentile([], 0.50) == 0.0
    assert harness.percentile([7.0], 0.95) == 7.0
    # 정렬되지 않은 입력에도 같은 답 — 측정 순서에 의존하지 않는다.
    assert harness.percentile(list(reversed(values)), 0.95) == 95.0


def test_ac043_breach_flag_only_trips_above_the_design_limit(harness: Any) -> None:
    """설계서 §8: CI 는 목표치가 아니라 **파탄 상한(p95 > 1000ms)** 에서만 실패한다."""
    target = harness.Target("x", 50.0, 120.0)

    fast = harness.summarize(target, [1.0] * 20)
    assert fast["breached"] is False and fast["within_target"] is True

    slow = harness.summarize(target, [200.0] * 20)
    assert slow["within_target"] is False, "목표를 넘었는데 within_target 이 참이다"
    assert slow["breached"] is False, "목표 초과를 파탄으로 셌다 — 간헐적 CI 실패의 원인이 된다"

    broken = harness.summarize(target, [2000.0] * 20)
    assert broken["breached"] is True


# ── AC-043 : 리포트 파일을 남긴다 ─────────────────────────────────────────
def test_ac043_main_writes_both_report_files(harness: Any, tmp_path: Path) -> None:
    """AC-043: 실행하면 기계용 `perf.json` 과 사람용 `perf.md` 가 생긴다."""
    out = tmp_path / "reports"
    code = harness.main(["--samples", "5", "--warmup", "1", "--out", str(out)])
    assert code == 0, "하네스가 실패를 보고했다 (파탄 상한 초과 — 알고리즘 문제 신호)"

    document = json.loads((out / "perf.json").read_text(encoding="utf-8"))
    assert document["spot_count"] == 27
    assert {item["samples"] for item in document["targets"]} == {5}

    markdown = (out / "perf.md").read_text(encoding="utf-8")
    assert "p50" in markdown and "p95" in markdown
    assert document["python"] in markdown and document["platform"] in markdown
    for item in document["targets"]:
        assert item["name"] in markdown, f"{item['name']} 가 사람용 리포트에 없다"


def test_ac043_committed_report_has_the_same_shape(harness: Any) -> None:
    """AC-043: 저장소에 커밋된 리포트도 같은 계약을 따른다(빈 껍데기가 아니다)."""
    assert COMMITTED_JSON.is_file() and COMMITTED_MD.is_file(), "reports/ 산출물이 없다 (AC-043)"
    document = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))

    assert document["spot_count"] == 27
    assert document["python"] and document["platform"]
    assert len(document["targets"]) >= 2
    for item in document["targets"]:
        assert set(item) == REQUIRED_TARGET_FIELDS
        assert item["samples"] >= 1
        assert item["min_ms"] <= item["p50_ms"] <= item["p95_ms"] <= item["max_ms"], item
        assert item["breached"] is False, (
            f"커밋된 리포트가 파탄 상한을 넘었다: {item['name']} p95={item['p95_ms']}ms"
        )


def test_process_time_header_is_what_the_harness_measures(client: Any) -> None:
    """AC-043 전제: 측정 대상은 **서버 핸들러 내부 처리시간**이다(§8).

    미들웨어가 빠지면 하네스는 `RuntimeError` 로 죽는다 — 그 신호를 여기서 먼저 잡는다.
    """
    response = client.get("/api/health")
    assert "X-Process-Time" in response.headers, "X-Process-Time 미들웨어가 빠졌다 (DSN-23)"
    assert float(response.headers["X-Process-Time"]) >= 0.0
