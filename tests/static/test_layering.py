"""계층 경계 정적 검사 — AC-048 (설계서 §2.2 DSN-02 · §12 F10).

설계는 `domain/` 을 **순수 계산**으로 못박았다. DB 도, HTTP 도, 시각 조회도 하지 않는다.
그 규칙을 지키는 방법은 산문으로 적어 두는 것이 아니라 **깨지는 순간 실패시키는 것**이다.

검사는 grep 이 아니라 AST 로 한다. 이유는 이 저장소에 실물 사례가 있다 —
`domain/__init__.py` 의 **주석**에 "httpx 를 import 하지 않는다"는 문장이 있다.
문자열 검사는 그것을 위반으로 읽는다. AST 는 import 문만 본다.

`domain/` 이 계층을 넘으면 무엇이 조용히 망가지나:
  * 단위 테스트가 DB 파일이나 네트워크를 요구하게 되고(밀리초 → 초),
  * 시각을 스스로 읽는 순간 같은 입력에 다른 출력이 나온다(AC-048 결정론이 깨진다).
둘 다 "테스트는 통과하는데 느리고 가끔 실패하는" 상태로 나타난다 — 가장 늦게 발견되는 종류다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src" / "harbor_lantern"
DOMAIN = SRC / "domain"

# domain/ 이 손대면 안 되는 것들. 앞이 최상위 모듈명이다.
FORBIDDEN_IN_DOMAIN = {
    "harbor_lantern.storage": "저장소 계층 (도메인은 DB 를 모른다)",
    "harbor_lantern.services": "서비스 계층 (의존 방향이 반대다)",
    "harbor_lantern.api": "HTTP 표면 (의존 방향이 반대다)",
    "httpx": "아웃바운드 HTTP (services/external 에서만)",
    "sqlite3": "DB 드라이버 (storage 에서만)",
    "fastapi": "웹 프레임워크",
    "requests": "아웃바운드 HTTP",
    "urllib": "아웃바운드 HTTP",
    "zoneinfo": "HKT 는 UTC+8 고정 상수다 (설계서 §2.3 · §12 F10)",
}

# `harbor_lantern.config` 는 **금지 목록에 없다.** 설계서 §6.7·§6.8 의 시그니처가
# `leg(a, b, cfg: TravelConfig)` · `resolve_dwell(spot, cfg)` 이라, 도메인은 설정을
# 인자로 받되 그 **타입**은 config.py 에서 가져온다. config.py 는 frozen dataclass 뿐이고
# I/O 가 없다(§6.1). 이것까지 막으면 설계가 정한 계약을 구현할 수 없다.

# httpx 는 어댑터 안에서만 산다(설계서 §2.2). 그 밖에서 나타나면 캐시·폴백을 우회하는 경로가 생긴 것이다.
HTTPX_ALLOWED_PREFIXES = ("services/external/",)


def _python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> list[tuple[str, int]]:
    """(모듈 경로, 줄번호) 목록. 상대 import 는 패키지 경로로 풀어 준다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = path.relative_to(SRC.parent).with_suffix("").parts
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # from . / .. import → 절대 경로로 환원
                base = package_parts[: len(package_parts) - node.level]
                module = ".".join([*base, node.module] if node.module else list(base))
            else:
                module = node.module or ""
            found.append((module, node.lineno))
    return found


def _violations(path: Path, forbidden: dict[str, str]) -> list[str]:
    out: list[str] = []
    for module, lineno in _imported_modules(path):
        for banned, why in forbidden.items():
            if module == banned or module.startswith(f"{banned}."):
                out.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno} → import {module} — {why}")
    return out


def test_domain_package_exists() -> None:
    files = _python_files(DOMAIN)
    if not files:
        pytest.skip("domain/ 이 아직 없다 (T1 · IMP-A 진행 중)")
    assert len(files) >= 3, f"domain/ 모듈이 너무 적다: {[f.name for f in files]}"


def test_domain_is_pure() -> None:
    """domain/** 이 storage·services·api·httpx·sqlite3 를 import 하지 않는다 (AC-048)."""
    files = _python_files(DOMAIN)
    if not files:
        pytest.skip("domain/ 이 아직 없다 (T1 · IMP-A 진행 중)")
    offenses: list[str] = []
    for path in files:
        offenses += _violations(path, FORBIDDEN_IN_DOMAIN)
    assert not offenses, "도메인 순수성이 깨졌다 (설계서 §2.2 · AC-048):\n  " + "\n  ".join(offenses)


def test_domain_does_not_read_the_clock() -> None:
    """`datetime.now()` · `date.today()` · `time.time()` 호출 금지 (NFR-013 · AC-048).

    도메인이 시각을 스스로 읽으면 같은 입력에 다른 출력이 나온다. 필요한 시각은 인자로 들어온다.
    """
    files = _python_files(DOMAIN)
    if not files:
        pytest.skip("domain/ 이 아직 없다 (T1 · IMP-A 진행 중)")
    banned_calls = {("datetime", "now"), ("datetime", "utcnow"), ("date", "today"), ("time", "time")}
    offenses: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                name = getattr(owner, "id", getattr(owner, "attr", None))
                if (name, node.func.attr) in banned_calls:
                    offenses.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} → {name}.{node.func.attr}()")
    assert not offenses, "도메인이 현재 시각을 직접 읽는다 (NFR-013):\n  " + "\n  ".join(offenses)


def test_httpx_is_confined_to_external_adapters() -> None:
    """아웃바운드 HTTP 는 services/external/ 안에서만 일어난다 (설계서 §2.2)."""
    files = _python_files(SRC)
    if not files:
        pytest.skip("src/ 가 아직 없다")
    offenses: list[str] = []
    for path in files:
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith(HTTPX_ALLOWED_PREFIXES):
            continue
        for module, lineno in _imported_modules(path):
            if module == "httpx" or module.startswith("httpx."):
                offenses.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}")
    assert not offenses, (
        "services/external/ 밖에서 httpx 를 import 한다 — 캐시·폴백(DSN-20)을 우회하는 경로다:\n  "
        + "\n  ".join(offenses)
    )


def test_web_assets_contain_no_python_imports_of_backend() -> None:
    """프론트는 서버 모듈을 모른다 — web/ 아래에 파이썬 모듈이 끼어들면 계층이 섞인 것이다."""
    web = SRC / "web"
    if not web.is_dir():
        pytest.skip("web/ 이 아직 없다")
    strays = [p.relative_to(PROJECT_ROOT).as_posix() for p in web.rglob("*.py")]
    assert not strays, f"web/ 아래에 파이썬 파일이 있다: {strays}"
