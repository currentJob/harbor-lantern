"""성능 측정 하네스 — DSN-23 (설계서 §8 · NFR-010 · AC-043).

**여기 적히는 숫자는 전부 이 스크립트가 방금 잰 것이다.** 설계서 §8 의 표는 목표치이지
측정값이 아니고, 절대치는 실행 환경에 의존한다(A6). 그래서 리포트에는 파이썬 버전·플랫폼·
표본수를 함께 남긴다 — 환경을 안 적은 성능 수치는 비교할 수 없고, 비교할 수 없는 수치는
없느니만 못하다.

측정 대상은 **서버 핸들러 내부 처리시간**이다(네트워크·브라우저 제외). 앱 미들웨어가
`X-Process-Time`(ms)으로 노출하고 이 하네스가 그것을 수집한다. `TestClient` 는 ASGI
인프로세스 전송이라 소켓을 열지 않는다 — 측정에 네트워크가 섞이지 않는다(NFR-003).

**CI 는 목표치로 실패하지 않는다.** 러너 성능이 흔들리면 게이트가 간헐적으로 빨개지고,
간헐적 실패는 아무도 안 보게 된다. 실패는 파탄 상한(p95 > 1000ms)에서만 — 그건 성능
문제가 아니라 알고리즘이 잘못됐다는 신호다.

    uv run python tools/perf_report.py [--samples 200] [--warmup 20]
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from harbor_lantern.api.app import create_app  # noqa: E402
from harbor_lantern.clock import SystemClock  # noqa: E402
from harbor_lantern.config import load_settings  # noqa: E402

DEFAULT_SAMPLES = 200
DEFAULT_WARMUP = 20
BREACH_LIMIT_MS = 1000.0  # 설계서 §8 — 이 위에서만 CI 가 실패한다


@dataclass(frozen=True)
class Target:
    """측정 한 종목. `target_*` 은 설계서 §8 의 **목표치**이지 보증이 아니다."""

    name: str
    target_p50_ms: float
    target_p95_ms: float


def percentile(values: Sequence[float], q: float) -> float:
    """최근접 순위(nearest-rank) 방식. 표본이 200개라 보간할 이유가 없고,
    **실제로 관측된 값**을 그대로 내는 편이 리포트를 읽을 때 덜 헷갈린다."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(-(-q * len(ordered) // 1)) - 1))
    return ordered[index]


def summarize(target: Target, samples: Sequence[float]) -> dict[str, Any]:
    p95 = percentile(samples, 0.95)
    return {
        "name": target.name,
        "samples": len(samples),
        "min_ms": round(min(samples), 3) if samples else 0.0,
        "p50_ms": round(percentile(samples, 0.50), 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(percentile(samples, 0.99), 3),
        "max_ms": round(max(samples), 3) if samples else 0.0,
        "mean_ms": round(sum(samples) / len(samples), 3) if samples else 0.0,
        "target_p50_ms": target.target_p50_ms,
        "target_p95_ms": target.target_p95_ms,
        "within_target": percentile(samples, 0.50) <= target.target_p50_ms and p95 <= target.target_p95_ms,
        "breach_limit_ms": BREACH_LIMIT_MS,
        "breached": p95 > BREACH_LIMIT_MS,
    }


def _server_ms(response: Any) -> float:
    """미들웨어가 단 `X-Process-Time`(ms). 없으면 측정이 성립하지 않으므로 즉시 실패한다."""
    header = response.headers.get("X-Process-Time")
    if header is None:
        raise RuntimeError("X-Process-Time 헤더가 없다 — 미들웨어가 빠졌는지 확인하라 (DSN-23)")
    return float(header)


def measure(call: Callable[[], Any], samples: int, warmup: int) -> list[float]:
    for _ in range(warmup):
        response = call()
        if response.status_code >= 400:
            raise RuntimeError(f"워밍업 요청이 실패했다: {response.status_code} {response.text[:200]}")
    return [_server_ms(call()) for _ in range(samples)]


def run(samples: int = DEFAULT_SAMPLES, warmup: int = DEFAULT_WARMUP) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="harbor-lantern-perf-") as workdir:
        settings = replace(load_settings(env={}), db_path=Path(workdir) / "perf.db")
        app = create_app(settings=settings, clock=SystemClock())
        with TestClient(app) as client:
            created = client.post("/api/trips", json={"organizer_display_name": "perf"})
            created.raise_for_status()
            document = created.json()
            trip_id = document["trip"]["id"]
            headers = {"X-Participant-Token": document["participant_token"]}
            state = client.get(f"/api/trips/{trip_id}/state", headers=headers)
            state.raise_for_status()
            spot_count = sum(len(day["spots"]) for day in state.json()["days"])

            results = [
                summarize(
                    Target("GET /api/trips/{trip_id}/state", 50.0, 120.0),
                    measure(
                        lambda: client.get(f"/api/trips/{trip_id}/state", headers=headers),
                        samples,
                        warmup,
                    ),
                ),
                summarize(
                    # Day 2 는 자유 스팟 9개 — 완전 탐색을 포기하고 2-opt 로 가는 쪽이다(§6.16).
                    Target("POST /api/trips/{trip_id}/days/2/optimize", 150.0, 150.0),
                    measure(
                        lambda: client.post(f"/api/trips/{trip_id}/days/2/optimize", headers=headers),
                        samples,
                        warmup,
                    ),
                ),
            ]

    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "measurement": "서버 핸들러 내부 처리시간 (X-Process-Time, ms). 네트워크·브라우저 제외",
        "warmup": warmup,
        "spot_count": spot_count,
        "targets": results,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 성능 측정 리포트 (AC-043 · NFR-010)",
        "",
        "이 표는 **아래 환경에서 방금 측정한 값**이다. 설계서 §8 의 목표치는 보증이 아니라 목표이고,",
        "절대치는 실행 환경에 의존한다(가정 A6).",
        "",
        f"- 측정 시각: `{report['generated_at']}`",
        f"- Python: `{report['python']}` · 플랫폼: `{report['platform']}`",
        f"- 표본: 종목당 {report['targets'][0]['samples']}회 (워밍업 {report['warmup']}회 제외)",
        f"- 시드 스팟 수: {report['spot_count']}",
        f"- 측정 대상: {report['measurement']}",
        "",
        "| 종목 | p50 | p95 | p99 | 최소 | 최대 | 목표 p50 | 목표 p95 | 목표 충족 | 파탄 상한(1000ms) |",
        "|------|-----|-----|-----|------|------|---------|---------|----------|------------------|",
    ]
    for item in report["targets"]:
        lines.append(
            f"| `{item['name']}` | {item['p50_ms']:.2f} ms | {item['p95_ms']:.2f} ms | {item['p99_ms']:.2f} ms |"
            f" {item['min_ms']:.2f} ms | {item['max_ms']:.2f} ms | ≤ {item['target_p50_ms']:.0f} ms |"
            f" ≤ {item['target_p95_ms']:.0f} ms | {'예' if item['within_target'] else '아니오'} |"
            f" {'초과' if item['breached'] else '이내'} |"
        )
    lines += [
        "",
        "CI 는 목표치로 실패하지 않는다 — 러너 성능이 흔들리면 게이트가 간헐적으로 빨개지고,",
        "간헐적 실패는 아무도 안 보게 된다. 실패는 파탄 상한(p95 > 1000ms)에서만이다.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor Lantern 성능 측정 (DSN-23)")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "reports")
    args = parser.parse_args(argv)

    report = run(samples=args.samples, warmup=args.warmup)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "perf.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (args.out / "perf.md").write_text(to_markdown(report), encoding="utf-8", newline="\n")

    for item in report["targets"]:
        print(
            f"{item['name']}: p50={item['p50_ms']:.2f}ms p95={item['p95_ms']:.2f}ms "
            f"p99={item['p99_ms']:.2f}ms (n={item['samples']})"
        )
    breached = [item["name"] for item in report["targets"] if item["breached"]]
    if breached:
        print(f"파탄 상한 초과: {breached}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
