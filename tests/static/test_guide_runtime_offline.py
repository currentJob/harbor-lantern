"""가이드 런타임의 외부 호출 0건 — NFR-017 · AC-079 (설계서 §16.2 · §16.12).

조사는 빌드 타임에 끝났고 런타임은 **파일을 읽을 뿐**이다. 그 약속을 산문으로만 적어
두면 다음 사람이 `services/external/discovery.py` 를 하나 부르는 것으로 조용히 깬다 —
그리고 그것은 테스트에서 안 보인다(가짜 공급자가 답해 주므로). 파리 한 도시를 런타임에
조사하려다 76초 만에 504 를 낸 것이 이 규칙의 출처다(`docs/_recon.md`).

그래서 **import 그래프로 잰다.** 가이드 경로가 닿는 우리 모듈 전체를 훑어 네트워크를
열 수 있는 모듈이 하나라도 들어오면 실패한다. 호출을 세는 것이 아니라 **닿을 수 있는지**
를 보는 이유는, 닿을 수 있으면 언젠가 닿기 때문이다.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"

# 가이드 경로의 진입점들. 여기서 출발해 닿는 우리 모듈 전부를 검사한다.
ENTRY_POINTS = (
    SRC / "harbor_lantern" / "services" / "guides.py",
    SRC / "harbor_lantern" / "domain" / "guide.py",
    SRC / "harbor_lantern" / "domain" / "cluster.py",
    SRC / "harbor_lantern" / "domain" / "guide_grade.py",
)

# 네트워크를 열 수 있는 것들. 앞이 최상위 모듈명이다.
NETWORK_CAPABLE = {
    "httpx": "아웃바운드 HTTP",
    "requests": "아웃바운드 HTTP",
    "urllib": "아웃바운드 HTTP",
    "http": "표준 HTTP 클라이언트",
    "socket": "소켓",
    "ssl": "TLS (소켓이 있다는 뜻)",
    "harbor_lantern.services.external": "외부 공급자 어댑터 (조사는 빌드 타임에 끝났다)",
}


def _module_path(module: str) -> Path | None:
    """`harbor_lantern.x.y` → 파일 경로. 우리 패키지가 아니면 `None`."""
    if not module.startswith("harbor_lantern"):
        return None
    base = SRC.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = path.relative_to(SRC).with_suffix("").parts
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level]
                module = ".".join([*base, node.module] if node.module else list(base))
            else:
                module = node.module or ""
            found.append((module, node.lineno))
    return found


def _reachable() -> dict[Path, list[tuple[str, int]]]:
    """진입점에서 닿는 우리 모듈 → 그 모듈의 import 목록."""
    seen: dict[Path, list[tuple[str, int]]] = {}
    queue = [path for path in ENTRY_POINTS if path.is_file()]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        imports = _imports(path)
        seen[path] = imports
        for module, _ in imports:
            target = _module_path(module)
            if target is not None and target not in seen:
                queue.append(target)
    return seen


def test_entry_points_exist() -> None:
    """진입점이 사라지면 아래 검사는 아무것도 검사하지 않는다 — 조용히 통과하는 게이트는 게이트가 아니다."""
    missing = [path.name for path in ENTRY_POINTS if not path.is_file()]
    assert not missing, f"가이드 경로 모듈이 없다: {missing}"


def test_guide_runtime_cannot_reach_the_network() -> None:
    reachable = _reachable()
    assert len(reachable) >= len(ENTRY_POINTS)

    violations: list[str] = []
    for path, imports in sorted(reachable.items()):
        for module, line in imports:
            for banned, why in NETWORK_CAPABLE.items():
                if module == banned or module.startswith(f"{banned}."):
                    rel = path.relative_to(PROJECT_ROOT).as_posix()
                    violations.append(f"{rel}:{line} → {module} ({why})")
    assert not violations, "가이드 런타임이 네트워크에 닿는다 (NFR-017 · AC-079):\n" + "\n".join(violations)


def test_loader_reads_one_directory_and_nothing_else() -> None:
    """로더의 I/O 는 `seed/city-guides/` 읽기 하나다. 쓰기도 실행도 없다."""
    source = (SRC / "harbor_lantern" / "services" / "guides.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    forbidden = {"write_text", "write_bytes", "mkdir", "unlink", "system", "run", "popen"}
    assert not (calls & forbidden), f"로더가 파일을 쓰거나 실행한다: {sorted(calls & forbidden)}"
    assert "read_text" in calls


def test_baked_guides_are_optional_at_rest() -> None:
    """`seed/city-guides/` 가 없어도 저장소는 정상이다 — 베이커(T8)가 만든다.

    이 테스트는 디렉터리의 **존재를 요구하지 않는다.** 있으면 이름 규칙만 본다.
    """
    directory = PROJECT_ROOT / "seed" / "city-guides"
    if not directory.is_dir():
        return
    from harbor_lantern.services.guides import is_city_id

    bad = [
        path.name
        for path in directory.glob("*.json")
        if path.stem not in {"index", "harvest-report"} and not is_city_id(path.stem)
    ]
    assert not bad, f"도시 파일 이름이 `[a-z0-9-]+` 가 아니다: {bad}"
