"""시크릿 · 의존성 고정 검사 — AC-041 · AC-045 · AC-046 (NFR-002 · NFR-011 · NFR-016).

두 가지를 본다.

1. **저장소에 비밀값이 없다.** 이 프로젝트의 외부 공급자(Open-Meteo · Frankfurter)는
   둘 다 키가 필요 없다(설계서 §6.13). 그래서 지금은 넣을 키가 없는데 —
   *지금 없다*는 것은 아무것도 보장하지 않는다. 장래에 키가 필요한 공급자로 바꿀 때
   가장 쉬운 길이 소스에 문자열로 박는 것이기 때문이다. 검사는 그때를 위해 있다.

2. **의존성이 정확한 버전으로 고정돼 있다.** 떠 있는 범위(`^` `~` `*` `latest`)는
   "어제 통과한 빌드가 오늘 실패한다"를 만든다. 실패하면 그나마 낫고, 조용히 동작이
   달라지는 쪽이 더 나쁘다.

이 파일 자신은 시크릿 패턴을 **문자열로 들고 있으므로** 스캔 대상에서 제외한다.
검사기가 자기 자신을 잡는 것은 거짓 실패이고, 거짓 실패를 한 번 두면
'원래 실패하는 검사'가 되어 진짜가 왔을 때 아무도 안 본다.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
LOCKFILE = PROJECT_ROOT / "uv.lock"
SELF = Path(__file__).resolve()

SCAN_ROOTS = ("src", "tools", "tests", "seed", "contracts", ".github/workflows")
SCAN_ROOT_FILES = ("README.md", "pyproject.toml", ".gitignore", ".gitattributes")

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", "vendor"}
TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".css", ".html", ".json", ".yaml", ".yml", ".toml", ".md", ".txt",
    ".sql", ".cfg", ".ini", ".sh", ".ps1",
}
MAX_BYTES = 1_000_000

# 고엔트로피 리터럴 또는 널리 쓰이는 공급자 키 포맷만 본다.
# "token" 같은 단어 자체는 잡지 않는다 — X-Participant-Token 은 헤더 이름이지 비밀이 아니다.
SECRET_PATTERNS = (
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS 액세스 키"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "개인키 블록"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "GitHub 토큰"),
    (re.compile(r"\bsk-[A-Za-z0-9]{24,}"), "OpenAI 형식 키"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{15,}"), "Slack 토큰"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "Google API 키"),
    (
        re.compile(
            r"""(?i)\b(api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token|password)\b"""
            r"""\s*[:=]\s*["'][A-Za-z0-9_\-/+]{16,}["']""",
        ),
        "하드코딩된 자격증명",
    ),
)

FLOATING_MARKERS = ("^", "~", "*", "latest")


def _scan_files() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_ROOT_FILES:
        path = PROJECT_ROOT / name
        if path.is_file():
            files.append(path)
    for root in SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path.resolve() == SELF:
                continue  # 검사기 자신 (패턴 문자열을 들고 있다)
            if path.stat().st_size > MAX_BYTES:
                continue
            files.append(path)
    return files


# ── AC-046 : 시크릿 ───────────────────────────────────────────────────────
def test_scan_covers_a_meaningful_number_of_files() -> None:
    """스캔 대상이 0건이면 그 아래 모든 검사가 '항상 통과'가 된다."""
    files = _scan_files()
    assert len(files) >= 20, f"스캔 대상이 너무 적다 ({len(files)}건) — 경로 설정이 틀렸을 가능성이 높다"


def test_no_secret_patterns_in_repository() -> None:
    offenses: list[str] = []
    for path in _scan_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenses.append(f"{path.relative_to(PROJECT_ROOT)}:{line} — {label}")
    assert not offenses, "저장소에 비밀값 패턴이 있다 (NFR-011 · AC-046):\n  " + "\n  ".join(offenses)


def test_external_provider_settings_come_from_environment() -> None:
    """외부 공급자 설정이 환경변수에서만 로드되는지 (AC-046)."""
    config = PROJECT_ROOT / "src" / "harbor_lantern" / "config.py"
    if not config.is_file():
        pytest.skip("config.py 가 아직 없다")
    text = config.read_text(encoding="utf-8")
    assert "HL_" in text, "설정이 HL_* 환경변수를 읽지 않는다"
    assert not re.search(r"""(?i)(api_key|apikey|token)\s*=\s*["'][^"']+["']""", text), (
        "config.py 에 하드코딩된 키가 있다"
    )


# ── AC-041 : 의존성 고정 ─────────────────────────────────────────────────
def _requirements() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirements = list(data.get("project", {}).get("dependencies", []))
    for group in data.get("dependency-groups", {}).values():
        requirements += [entry for entry in group if isinstance(entry, str)]
    build = data.get("build-system", {}).get("requires", [])
    requirements += [entry for entry in build if isinstance(entry, str)]
    return requirements


def test_pyproject_exists_and_has_dependencies() -> None:
    assert PYPROJECT.is_file(), "pyproject.toml 이 없다"
    assert _requirements(), "의존성이 하나도 없다 — 검사가 무의미해진다"


def test_every_dependency_is_pinned_exactly() -> None:
    unpinned: list[str] = []
    for requirement in _requirements():
        spec = requirement.split(";")[0].strip()          # 환경 마커 제거
        version_part = re.sub(r"^[A-Za-z0-9._\-]+(\[[^\]]*\])?", "", spec).strip()
        if "==" not in version_part:
            unpinned.append(requirement)
            continue
        if any(marker in version_part for marker in FLOATING_MARKERS):
            unpinned.append(requirement)
    assert not unpinned, (
        "정확히 고정되지 않은 의존성이 있다 (NFR-002 · AC-041): " + ", ".join(unpinned)
    )


def test_python_requirement_is_bounded() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requires_python = data.get("project", {}).get("requires-python", "")
    assert "<" in requires_python, (
        f"requires-python 에 상한이 없다: {requires_python!r} — 검증되지 않은 미래 버전이 자동으로 들어온다"
    )


def test_lockfile_is_committed() -> None:
    assert LOCKFILE.is_file(), "uv.lock 이 없다 (NFR-002 · AC-041 — 재현성이 사라진다)"
    assert LOCKFILE.stat().st_size > 1000, "uv.lock 이 비정상적으로 작다"
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    ignored = [line.strip() for line in gitignore.splitlines()
               if line.strip() and not line.strip().startswith("#")]
    assert "uv.lock" not in ignored, ".gitignore 가 uv.lock 을 제외하고 있다"


# ── AC-045 : 라이선스 ────────────────────────────────────────────────────
def test_mit_license_file_exists() -> None:
    license_file = PROJECT_ROOT / "LICENSE"
    assert license_file.is_file(), "LICENSE 가 없다 (NFR-016 · AC-045)"
    assert "MIT License" in license_file.read_text(encoding="utf-8")


def test_readme_states_the_license() -> None:
    readme = PROJECT_ROOT / "README.md"
    assert readme.is_file(), "README.md 가 없다 (AC-045)"
    assert "MIT" in readme.read_text(encoding="utf-8"), "README 에 라이선스 표기가 없다 (AC-045)"
