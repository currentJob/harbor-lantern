#!/usr/bin/env python3
"""로컬 품질 게이트 실행기 (release-engineering.md §2-1).

**생성된 파일이다. 프로젝트에서 직접 고치지 말 것** — 하네스가 빌드마다 덮어쓴다.
원본: `.claude/skills/product-build-orchestrator/assets/scripts/gate.py`

## 왜 있나

검증 명령이 여섯 개면 여섯 번 실행하고 여섯 번의 출력을 읽어야 한다. 대부분은 "통과"라는
한 줄이면 충분한데도 그렇다. 이 스크립트는 **`.github/quality/gate.json` 에 적힌 단계를
전부 조용히 돌리고 요약 한 줄만 낸다.** 실패했을 때만 그 단계의 출력 꼬리를 보여 준다.

읽을 것이 없으면 읽지 않는 것이 가장 싸다.

## gate.json

```json
{
  "requiredEnv": ["ANDROID_HOME", "JAVA_HOME"],
  "steps": [
    { "name": "단위 테스트", "cmd": ["./gradlew", "testDebugUnitTest", "--offline", "-q"] }
  ]
}
```

`requiredEnv` 가 비어 있으면 `.env.local`(gitignore 대상)에서 `KEY=VALUE` 로 읽어 채운다.
매번 셸에 환경 변수를 손으로 넣던 일을 없애기 위한 것이다.

사용:
    python .github/quality/gate.py            # 전체 실행, 요약 한 줄
    python .github/quality/gate.py --verbose  # 모든 단계 출력까지
    python .github/quality/gate.py --only lint

종료 코드: 0 = 전 단계 통과 / 1 = 실패 / 2 = 설정 오류.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TAIL_LINES = 30


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load_env_local(root: Path) -> dict[str, str]:
    """`.env.local` 에서 KEY=VALUE 를 읽는다. 없으면 빈 dict.

    기기·계정마다 다른 경로(SDK 위치 등)라 저장소에 넣을 수 없다. 그렇다고 매번 셸에
    손으로 넣으면 잊어버린다. 그 사이를 메우는 파일이다(gitignore 대상).
    """
    f = root / ".env.local"
    if not f.is_file():
        return {}
    out: dict[str, str] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def resolve_cmd(cmd: list[str], root: Path) -> list[str]:
    """`./gradlew` 같은 상대 실행 파일을 OS 에 맞게 바꾼다."""
    head = cmd[0]
    if head in ("./gradlew", "gradlew"):
        bat = root / "gradlew.bat"
        if os.name == "nt" and bat.is_file():
            return [str(bat), *cmd[1:]]
        return [str(root / "gradlew"), *cmd[1:]]
    resolved = shutil.which(head)
    return [resolved or head, *cmd[1:]]


def run_step(step: dict, root: Path, env: dict[str, str], verbose: bool) -> tuple[bool, str, float]:
    started = time.monotonic()
    cmd = resolve_cmd(list(step["cmd"]), root)
    try:
        r = subprocess.run(
            cmd, cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=step.get("timeout", 1800),
        )
    except FileNotFoundError:
        return False, f"실행 파일을 찾을 수 없음: {cmd[0]}", time.monotonic() - started
    except subprocess.SubprocessError as e:
        return False, f"실행 실패: {e}", time.monotonic() - started
    output = r.stdout.decode("utf-8", "replace")
    if verbose and output.strip():
        print(output.rstrip())
    return r.returncode == 0, output, time.monotonic() - started


def main(argv: list[str]) -> int:
    force_utf8()
    ap = argparse.ArgumentParser(description="프로젝트 품질 게이트를 한 번에 실행한다")
    ap.add_argument("--config", default=".github/quality/gate.json")
    ap.add_argument("--only", help="이름에 이 문자열이 든 단계만 실행")
    ap.add_argument("--verbose", action="store_true", help="통과한 단계의 출력도 모두 표시")
    args = ap.parse_args(argv[1:])

    root = Path.cwd().resolve()
    cfg_path = root / args.config
    if not cfg_path.is_file():
        print(f"오류: 게이트 설정을 찾을 수 없음: {args.config}")
        return 2
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"오류: gate.json 파싱 실패 — {e}")
        return 2

    env = {**os.environ, **load_env_local(root)}
    missing = [k for k in cfg.get("requiredEnv", []) if not env.get(k)]
    if missing:
        print(f"오류: 환경 변수 누락 — {', '.join(missing)}")
        print(f"      {root / '.env.local'} 에 KEY=VALUE 로 적거나 셸에서 export 하세요.")
        return 2

    steps = cfg.get("steps", [])
    if args.only:
        steps = [s for s in steps if args.only in s["name"]]
    if not steps:
        print("오류: 실행할 단계가 없습니다")
        return 2

    total = time.monotonic()
    for i, step in enumerate(steps, 1):
        ok, output, took = run_step(step, root, env, args.verbose)
        if not ok:
            print(f"■ 게이트 {i - 1}/{len(steps)} — 실패: {step['name']} ({took:.0f}s)")
            print(f"--- 마지막 {TAIL_LINES}줄 ---")
            print("\n".join(output.splitlines()[-TAIL_LINES:]))
            return 1

    print(f"■ 게이트 {len(steps)}/{len(steps)} 통과 ({time.monotonic() - total:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
