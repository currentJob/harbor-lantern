#!/usr/bin/env python3
"""의존성 버전 고정 검증 (release-engineering.md §3).

**생성된 파일이다. 프로젝트에서 직접 고치지 말 것** — 하네스가 빌드마다 덮어쓴다.
원본: `.claude/skills/product-build-orchestrator/assets/scripts/check_pinning.py`

고정하지 않은 의존성은 "어제는 됐는데 오늘은 안 되는" 빌드를 만든다. 그리고 그 차이는
빌드가 깨질 때가 아니라 **조용히 동작이 바뀔 때** 가장 비싸다.

목적은 **재현성**이지 특정 표기법이 아니다. 그래서 규칙을 락파일 기준으로 둔다:

| 상황 | 판정 |
|------|------|
| 락파일 커밋됨 + 매니페스트에 캐럿/틸드(`^1.2`, `~1.2`) | **통과** — 락파일이 진실이고 `npm ci` 는 그대로 설치한다 |
| 락파일 없음 + 캐럿/틸드 | 위반 — 무엇이 설치될지 아무도 모른다 |
| 상한 없는 `>=` | **항상 위반** — 락파일이 있어도 갱신 시 어디까지 올라갈지 모른다 |
| `*` · `latest` · Gradle `+` · `latest.release` | 항상 위반 |

액션도 의존성이다. `uses: foo/bar@main` 은 남의 브랜치에 내 CI 를 맡기는 것이므로 같이 막는다.

종료 코드: 0 = 통과 / 1 = 위반.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# 스캔에서 제외할 디렉토리. 남의 소스까지 검사하면 노이즈만 는다.
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "build", "dist", "out", "target",
    ".gradle", ".venv", "venv", "__pycache__", ".idea", ".dart_tool",
}

# 매니페스트 → 락파일 후보(하나라도 있으면 "락파일 있음")
LOCKFILES: dict[str, list[str]] = {
    "package.json": ["package-lock.json", "pnpm-lock.yaml", "yarn.lock", "npm-shrinkwrap.json"],
    "pyproject.toml": ["uv.lock", "poetry.lock", "pdm.lock", "requirements.lock"],
    "Cargo.toml": ["Cargo.lock"],
    "go.mod": ["go.sum"],
    "Gemfile": ["Gemfile.lock"],
    "composer.json": ["composer.lock"],
    "pubspec.yaml": ["pubspec.lock"],
}

# 파이썬에서 **의존성이 아닌** 버전 제약. 인터프리터 하한이라 `>=` 가 정상이다.
# 이걸 빼지 않으면 `requires-python = ">=3.12"` 가 위반으로 잡힌다(실제로 잡혔다).
PY_NON_DEPENDENCY = re.compile(r"^\s*(requires-python|python_requires)\s*=", re.M)

# 파이썬 요구사항 문자열: `name>=1.2,<2` / `name[extra]==1.0` 등
PY_REQUIREMENT = re.compile(r'^[A-Za-z][A-Za-z0-9._-]*(\[[^\]]*\])?\s*[<>=!~]')

# 액션 참조: `@<브랜치>` 금지, `@v1.2.3` 또는 `@<40자 SHA>` 만 허용
ACTION_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.M)
ACTION_PINNED = re.compile(r"@(v?\d+(\.\d+)*|[0-9a-f]{40})$")

CARET_TILDE = re.compile(r"^\s*[\^~]\s*\d")
WILDCARD = re.compile(r"^\s*(\*|x|X)\s*$")
LATEST = re.compile(r"^\s*latest\s*$", re.I)
# Gradle 동적 버전: `1.+`, `1.2.+`, 드물게 `+`. 좌표의 버전 자리가 `+` 로 끝난다.
# 숫자 바로 앞만 보면 `1.+` 의 점 때문에 놓친다(테스트로 잡혔다).
GRADLE_DYNAMIC = re.compile(r"""[:'"]\s*[0-9.]*\+\s*['"]|latest\.(release|integration)""", re.I)


def has_upper_bound(spec: str) -> bool:
    """`<`, `<=`, `==`, `~=` 중 하나라도 있으면 위쪽이 막혀 있다."""
    return bool(re.search(r"<|==|~=", spec))


def is_unbounded_ge(spec: str) -> bool:
    """`>` 또는 `>=` 가 있는데 상한이 없다.

    정규식 하나로 `>=3.11,<3.14` 를 판정하려 하면 백트래킹 때문에 `>=3.1` 만 맞고
    상한을 놓친다(실제로 그랬다). 그래서 **먼저 상한 유무를 보고** 판단한다.
    """
    return bool(re.search(r">=?\s*\d", spec)) and not has_upper_bound(spec)


def tracked_files(root: Path) -> list[Path] | None:
    """git 이 추적하는 파일만. 저장소가 아니면 None.

    검사 범위는 **저장소에 들어 있는 것**이다. gitignore 된 산출물이나 하위 작업 폴더까지
    검사하면, 내가 책임지지도 않는 파일 때문에 게이트가 빨개진다.

    바이트로 받아 UTF-8 로 **직접** 디코딩한다. `text=True` 는 로케일 코덱을 쓰는데,
    Windows 한국어 환경(cp949)에서 한글 파일명을 만나면 읽기 스레드가 죽고 stdout 이
    조용히 None 이 된다 — 한글 문서명을 쓰는 이 하네스의 모든 프로젝트가 해당한다.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    decoded = r.stdout.decode("utf-8", "replace")
    return [root / rel for rel in decoded.split("\0") if rel]


def walk(root: Path):
    """검사 대상 파일. git 저장소면 추적 파일만, 아니면 파일시스템 순회.

    제외 판정은 **root 기준 상대 경로**로 한다. 절대 경로 전체를 보면 상위 디렉토리 이름이
    우연히 `build` 나 `dist` 이기만 해도 프로젝트 전체가 조용히 스킵된다.
    """
    tracked = tracked_files(root)
    candidates = tracked if tracked is not None else root.rglob("*")
    for p in candidates:
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.is_file():
            yield p


def lock_state(manifest: Path) -> tuple[bool, list[str]]:
    """(락파일 있음?, 기대 락파일 목록)"""
    locks = LOCKFILES.get(manifest.name, [])
    if not locks:
        return True, []  # 락파일 개념이 없는 매니페스트(Gradle 등)
    return any((manifest.parent / lock).exists() for lock in locks), locks


def check_npm(manifest: Path, has_lock: bool, rel: str, errors: list[str]) -> None:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        errors.append(f"{rel}: JSON 파싱 실패")
        return
    for block, deps in data.items():
        if "ependencies" not in block or not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            if not isinstance(spec, str):
                continue
            where = f"{rel} [{block}] {name}: {spec}"
            if WILDCARD.match(spec) or LATEST.match(spec):
                errors.append(f"{where} — 와일드카드/latest 는 항상 금지")
            elif is_unbounded_ge(spec):
                errors.append(f"{where} — 상한 없는 비교 연산자")
            elif CARET_TILDE.match(spec) and not has_lock:
                errors.append(f"{where} — 캐럿/틸드인데 락파일이 없다")


def check_python(manifest: Path, has_lock: bool, rel: str, errors: list[str]) -> None:
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for raw in text.splitlines():
        if PY_NON_DEPENDENCY.match(raw):
            continue  # 인터프리터 버전 제약은 의존성이 아니다
        # 따옴표 안의 요구사항 문자열, 또는 requirements.txt 의 맨 줄
        candidates = re.findall(r'["\']([^"\']+)["\']', raw)
        if manifest.name == "requirements.txt" and not candidates:
            stripped = raw.split("#", 1)[0].strip()
            candidates = [stripped] if stripped else []
        for spec in candidates:
            if not PY_REQUIREMENT.match(spec):
                continue
            where = f"{rel}: {spec}"
            if LATEST.search(spec):
                errors.append(f"{where} — latest 는 항상 금지")
            elif is_unbounded_ge(spec):
                errors.append(f"{where} — 상한 없는 비교 연산자")
            elif CARET_TILDE.search(spec) and not has_lock:
                errors.append(f"{where} — 캐럿/틸드인데 락파일이 없다")


def check_gradle(path: Path, rel: str, errors: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for m in GRADLE_DYNAMIC.finditer(text):
        errors.append(f"{rel}: 동적 버전 — {m.group(0).strip()}")
        return  # 파일당 한 번만 보고한다


def check_manifests(root: Path, errors: list[str], notes: list[str]) -> None:
    npm_like = {"package.json"}
    py_like = {"pyproject.toml", "requirements.txt"}
    gradle_like = {"build.gradle", "build.gradle.kts"}

    for p in walk(root):
        rel = str(p.relative_to(root))
        if p.name in gradle_like:
            check_gradle(p, rel, errors)
            continue
        if p.name not in npm_like | py_like | set(LOCKFILES):
            continue

        has_lock, locks = lock_state(p)
        if locks:
            if has_lock:
                notes.append(f"락파일 확인: {rel}")
            else:
                errors.append(f"{rel}: 락파일이 없습니다 (필요: {' 또는 '.join(locks)})")

        if p.name in npm_like:
            check_npm(p, has_lock, rel, errors)
        elif p.name in py_like:
            check_python(p, has_lock, rel, errors)


def check_actions(root: Path, errors: list[str], notes: list[str]) -> None:
    wf = root / ".github" / "workflows"
    if not wf.is_dir():
        return
    for p in sorted(wf.glob("*.y*ml")):
        rel = str(p.relative_to(root))
        text = p.read_text(encoding="utf-8")
        checked = 0
        for m in ACTION_USES.finditer(text):
            ref = m.group(1)
            if ref.startswith("./") or ref.startswith("docker://"):
                continue  # 로컬 워크플로 호출·명시 이미지 태그는 별도 규칙
            checked += 1
            if "@" not in ref:
                errors.append(f"{rel}: 버전 없는 액션 참조 — {ref}")
            elif not ACTION_PINNED.search(ref):
                errors.append(f"{rel}: 브랜치에 고정된 액션 — {ref} (버전 태그나 SHA 를 쓰세요)")
        if checked:
            notes.append(f"액션 고정 확인: {rel} ({checked}건)")


def run(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    check_manifests(root, errors, notes)
    check_actions(root, errors, notes)
    return errors, notes


def _force_utf8() -> None:
    """Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않게 한다(validate_docs.py 와 동일 처리)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str]) -> int:
    _force_utf8()
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    if not root.is_dir():
        print(f"오류: 디렉토리를 찾을 수 없음: {root}")
        return 2

    errors, notes = run(root)

    print(f"■ 의존성 고정 검증: {root}")
    print("-" * 60)
    for n in notes:
        print(f"  OK  {n}")
    for e in errors:
        print(f"  !!  {e}")
    print("-" * 60)
    if errors:
        print(f"결과: 위반 {len(errors)}건 — 락파일을 커밋하거나 상한을 두세요.")
        return 1
    print("결과: 위반 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
