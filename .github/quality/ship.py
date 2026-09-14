#!/usr/bin/env python3
"""커밋 → push → CI 대기 → 태그 → 릴리스를 한 번에 (release-engineering.md §5-1).

**생성된 파일이다. 프로젝트에서 직접 고치지 말 것** — 하네스가 빌드마다 덮어쓴다.
원본: `.claude/skills/product-build-orchestrator/assets/scripts/ship.py`

## 왜 있나

배포 한 번에 `git add` · `commit` · `push` · `gh run list` · `gh run watch` · `gh run view` ·
`git tag` · `git push --tags` · 다시 watch · `gh release view` — 열 번 넘게 명령을 친다.
그 사이 출력은 대부분 "진행 중"이거나 CRLF 경고 같은 잡음이다.

**기다리는 일은 사람이 읽을 필요가 없다.** 이 스크립트가 안에서 다 기다리고,
끝났을 때 결과 몇 줄만 낸다. 실패했을 때만 실패한 잡·스텝과 로그 꼬리를 보여 준다.

## 하는 일

1. 변경이 있으면 스테이징 + 커밋 (없으면 건너뛴다)
2. push (CRLF 경고 같은 잡음은 걸러낸다)
3. CI 완료까지 대기 → 실패면 실패 지점과 로그 꼬리를 내고 **거기서 멈춘다**
4. `--tag vX.Y` 가 있으면 태그 생성·push → 릴리스 워크플로 대기 → 릴리스 URL 출력

**CI 가 green 이 아니면 태그를 달지 않는다.** 게시 가드(§5.5)를 스크립트가 강제한다.

사용:
    python .github/quality/ship.py -m "커밋 메시지"
    python .github/quality/ship.py -m "..." --tag v1.8
    python .github/quality/ship.py --tag v1.8 --no-commit     # 이미 커밋된 것 배포
    python .github/quality/ship.py -m "..." --no-wait          # push 만 하고 끝

종료 코드: 0 = 성공 / 1 = CI 실패·릴리스 실패 / 2 = 사용법·환경 오류.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

POLL_SECONDS = 15
CI_TIMEOUT = 45 * 60
LOG_TAIL = 40

# git 이 stderr 로 내는 정보성 잡음. 매 push 마다 파일 수만큼 반복돼 출력을 잡아먹는다.
NOISE = ("LF will be replaced by CRLF", "CRLF will be replaced by LF", "warning: in the working copy")


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def run(*args: str, check: bool = True, stdin: str | None = None) -> tuple[int, str]:
    r = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        input=stdin.encode("utf-8") if stdin is not None else None,
    )
    out = r.stdout.decode("utf-8", "replace")
    if check and r.returncode != 0:
        print(f"오류: {' '.join(args[:3])} 실패\n{out.strip()[-800:]}")
        raise SystemExit(1)
    return r.returncode, out


def quiet(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not any(n in l for n in NOISE)).strip()


def gh_json(*args: str) -> object:
    _, out = run("gh", *args)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        print(f"오류: gh 응답을 읽지 못했습니다\n{out[:400]}")
        raise SystemExit(1)


def wait_run(workflow: str, head: str, label: str) -> tuple[bool, str]:
    """워크플로 실행이 끝날 때까지 기다린다. (성공?, run id)"""
    deadline = time.time() + CI_TIMEOUT
    run_id = ""
    while time.time() < deadline:
        rows = gh_json(
            "run", "list", "--workflow", workflow, "--limit", "5",
            "--json", "databaseId,status,conclusion,headSha,headBranch",
        )
        for r in rows:  # type: ignore[union-attr]
            if head in (r["headSha"], r["headBranch"]):
                run_id = str(r["databaseId"])
                if r["status"] == "completed":
                    return r["conclusion"] == "success", run_id
                break
        time.sleep(POLL_SECONDS)
    print(f"오류: {label} 대기 시간 초과")
    return False, run_id


def report_failure(run_id: str) -> None:
    data = gh_json("run", "view", run_id, "--json", "jobs")
    for job in data.get("jobs", []):  # type: ignore[union-attr]
        if job.get("conclusion") != "success":
            failed = [s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"]
            print(f"  실패 잡: {job['name']}" + (f" / 스텝: {', '.join(failed)}" if failed else ""))
    _, log = run("gh", "run", "view", run_id, "--log-failed", check=False)
    tail = [l for l in log.splitlines() if l.strip()][-LOG_TAIL:]
    if tail:
        print(f"--- 로그 마지막 {len(tail)}줄 ---")
        print("\n".join(tail))


def main(argv: list[str]) -> int:
    force_utf8()
    ap = argparse.ArgumentParser(description="커밋·push·CI·릴리스를 한 번에 수행한다")
    ap.add_argument("-m", "--message", help="커밋 메시지 (파일 경로면 그 내용을 쓴다)")
    ap.add_argument("--tag", help="CI 통과 후 붙일 릴리스 태그 (예: v1.8)")
    ap.add_argument("--no-commit", action="store_true", help="커밋하지 않고 이미 있는 커밋을 배포")
    ap.add_argument("--no-wait", action="store_true", help="push 만 하고 CI 를 기다리지 않는다")
    args = ap.parse_args(argv[1:])

    if not args.no_commit and not args.message:
        print("오류: -m 커밋 메시지가 필요합니다 (--no-commit 이면 생략 가능)")
        return 2

    lines: list[str] = []

    # 1) 커밋
    if not args.no_commit:
        _, status = run("git", "status", "--porcelain")
        changed = [l for l in status.splitlines() if l.strip()]
        if changed:
            run("git", "add", "-A")
            msg = args.message
            p = Path(msg)
            if p.is_file():
                msg = p.read_text(encoding="utf-8")
            run("git", "commit", "-F", "-", stdin=msg)
            lines.append(f"커밋 {len(changed)}개 파일")
        else:
            lines.append("변경 없음 — 커밋 생략")

    # 2) push
    _, branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    code, out = run("git", "push", "origin", branch, check=False)
    if code != 0:
        print(f"오류: push 실패\n{quiet(out)[-800:]}")
        return 1
    _, sha = run("git", "rev-parse", "HEAD")
    sha = sha.strip()
    lines.append(f"push {sha[:7]}")

    if args.no_wait:
        print("■ ship: " + " → ".join(lines))
        return 0

    # 3) CI 대기 — 여기가 이 스크립트의 존재 이유다. 기다림은 읽을 필요가 없다.
    ok, run_id = wait_run("ci.yml", sha, "CI")
    if not ok:
        print("■ ship: " + " → ".join(lines) + " → **CI 실패**")
        if run_id:
            report_failure(run_id)
        return 1
    lines.append("CI 통과")

    # 4) 태그 → 릴리스. CI 가 green 일 때만 여기 온다(게시 가드).
    if args.tag:
        code, out = run("git", "tag", args.tag, "-m", args.tag, check=False)
        if code != 0 and "already exists" in out:
            print(f"오류: 태그 {args.tag} 가 이미 있습니다. 태그는 재사용하지 않습니다(§8).")
            return 1
        run("git", "push", "origin", args.tag, check=False)
        ok, rel_id = wait_run("release.yml", args.tag, "릴리스")
        if not ok:
            print("■ ship: " + " → ".join(lines) + f" → 태그 {args.tag} → **릴리스 실패**")
            if rel_id:
                report_failure(rel_id)
            return 1
        lines.append(f"릴리스 {args.tag}")
        rel = gh_json("release", "view", args.tag, "--json", "url,assets")
        print("■ ship: " + " → ".join(lines))
        print(rel["url"])  # type: ignore[index]
        for a in rel.get("assets", []):  # type: ignore[union-attr]
            print(f"  첨부 {a['name']} {a['size'] / 1048576:.2f} MB")
        return 0

    print("■ ship: " + " → ".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
