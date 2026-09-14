"""프론트 자산 정적 검사 — AC-042 · AC-044 · 벤더 무결성 (설계서 DSN-22 · §12 F11 · F12).

이 파일이 잡는 것은 전부 "브라우저에서는 멀쩡해 보이는" 종류의 회귀다.

* CDN 링크는 개발자 브라우저에 캐시가 남아 있으면 끝까지 안 걸린다 — 비행기 안에서 걸린다.
* 벤더 파일의 바이트가 바뀌는 것(포매터·CRLF 변환)은 화면에 아무 표시가 없다.
* 터치 타깃 43.5px 은 눈으로 구별되지 않는다. 손가락으로만 구별된다.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB = PROJECT_ROOT / "src" / "harbor_lantern" / "web"
VENDOR = WEB / "vendor" / "leaflet-1.9.4"
INDEX = WEB / "hongkong.html"
CSS = WEB / "css" / "app.css"

# 런타임에만 나가는 외부 목적지. 정적 자산(<script src>·<link href>)에는 하나도 없어야 한다.
#   - 지도 타일: 원본과 같은 CARTO 다크 타일. 실패해도 앱은 죽지 않는다(설계서 §6.15 폴백).
#   - 구글맵 딥링크: 사용자가 누를 때 열리는 길찾기 URL. AC-034 가 이 형식을 요구한다.
ALLOWED_RUNTIME_HOSTS = {
    "{s}.basemaps.cartocdn.com",
    "www.google.com",
    # 지도 저작권 표기에 쓰이는 도메인 문자열이 필요해지면 여기에 명시적으로 추가한다.
}

URL_RE = re.compile(r"""https?://([^\s"'`)\\<>]+)""")


def _front_sources() -> list[Path]:
    files = [INDEX, CSS, WEB / "index.html", WEB / "css/explore.css"]
    files += sorted((WEB / "js").rglob("*.js"))
    return [path for path in files if path.is_file()]


def _hosts_in(text: str) -> set[str]:
    return {URL_RE.match(f"https://{m.group(1)}").group(1).split("/")[0] for m in URL_RE.finditer(text)}


# ── AC-042 : 외부 도메인 참조 0건 ─────────────────────────────────────────
def test_front_sources_exist() -> None:
    assert INDEX.is_file(), "index.html 이 없다"
    assert CSS.is_file(), "css/app.css 가 없다"
    assert (WEB / "js" / "main.js").is_file(), "js/main.js 가 없다"


def test_no_unexpected_external_hosts() -> None:
    offenders: list[str] = []
    for path in _front_sources():
        for host in _hosts_in(path.read_text(encoding="utf-8")):
            if host not in ALLOWED_RUNTIME_HOSTS:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} → {host}")
    assert not offenders, "외부 도메인 참조가 남았다 (NFR-015 · AC-042):\n  " + "\n  ".join(offenders)


def test_html_has_no_external_script_or_link() -> None:
    """<script src> · <link href> 는 **예외 없이** 저장소 안을 가리켜야 한다."""
    html = INDEX.read_text(encoding="utf-8")
    refs = re.findall(r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", html)
    assert refs, "index.html 이 자산을 하나도 참조하지 않는다 — 검사가 무의미해진다"
    external = [ref for ref in refs if ref.startswith(("http://", "https://", "//"))]
    assert not external, f"HTML 에 외부 자산 참조가 있다 (AC-042): {external}"
    for ref in refs:
        target = (WEB / ref.lstrip("./")).resolve()
        assert target.is_file(), f"index.html 이 없는 파일을 참조한다: {ref}"


def test_html_loads_vendored_leaflet_and_module_entry() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "./vendor/leaflet-1.9.4/leaflet.css" in html
    assert "./vendor/leaflet-1.9.4/leaflet.js" in html
    assert re.search(r"""<script[^>]+type=["']module["'][^>]+src=["']\./js/main\.js["']""", html), (
        "main.js 가 ES 모듈로 로드되지 않는다 (NFR-001 — 번들러 없이 브라우저가 그대로 읽어야 한다)"
    )


def test_no_bundler_artifacts() -> None:
    """번들러 설정·산출물이 존재하면 안 된다 (NFR-001 · AC-042)."""
    forbidden = [
        "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        "vite.config.js", "vite.config.ts", "webpack.config.js", "rollup.config.js",
        "node_modules",
    ]
    found = [name for name in forbidden if (PROJECT_ROOT / name).exists()]
    assert not found, f"빌드 체인 흔적이 있다 (NFR-001): {found}"

    # `dist/` 는 금지 목록에 둘 수 없다 — 프론트 번들러와 **파이썬 패키징**(`uv build`)이
    # 같은 이름을 쓰기 때문이다. 릴리스 워크플로가 sdist·wheel 을 여기에 만든다.
    # 그러니 존재 자체가 아니라 **내용물**로 판정한다: JS 번들이 떨어져 있으면 그건 빌드 체인이다.
    dist = PROJECT_ROOT / "dist"
    if dist.is_dir():
        # `uv build` 는 `dist/.gitignore` 도 같이 만든다 — 점파일은 산출물이 아니다.
        strays = [p.name for p in dist.iterdir()
                  if not p.name.startswith(".")
                  and p.suffix not in (".whl", ".gz", ".zip")
                  and not p.name.endswith(".tar.gz")]
        assert not strays, f"dist/ 에 파이썬 패키징 산출물이 아닌 것이 있다 (NFR-001): {strays}"


def test_js_relative_imports_resolve() -> None:
    """`import ... from './x.js'` 가 실제 파일을 가리키는지.

    번들러가 없으므로 경로 오타를 잡아 주는 도구도 없다. 브라우저에서 404 가 나야
    비로소 안다 — 그 전에 여기서 잡는다.
    """
    missing: list[str] = []
    for path in sorted((WEB / "js").rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        for spec in re.findall(r"""(?:import|export)[^'"]*?from\s+['"](\.[^'"]+)['"]""", source):
            if not (path.parent / spec).resolve().is_file():
                missing.append(f"{path.relative_to(PROJECT_ROOT)} → {spec}")
    assert not missing, "존재하지 않는 모듈을 import 한다:\n  " + "\n  ".join(missing)


# ── 벤더 무결성 (§12 F11) ─────────────────────────────────────────────────
def _load_vendor_check():
    tool = PROJECT_ROOT / "tools" / "vendor_check.py"
    spec = importlib.util.spec_from_file_location("vendor_check", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendor_manifest_matches_files() -> None:
    module = _load_vendor_check()
    problems = module.check()
    assert not problems, "Leaflet 벤더 파일이 매니페스트와 다르다:\n  " + "\n  ".join(problems)


def test_vendor_files_are_present_and_nonempty() -> None:
    for name in ("leaflet.js", "leaflet.css"):
        path = VENDOR / name
        assert path.is_file(), f"벤더 파일 누락: {name}"
        assert path.stat().st_size > 1000, f"벤더 파일이 비정상적으로 작다: {name}"
    assert (VENDOR / "images" / "marker-icon.png").is_file()


def test_vendor_css_has_no_external_url() -> None:
    """Leaflet CSS 가 이미지·폰트를 외부에서 끌어오지 않는지 (상대 경로여야 한다)."""
    text = (VENDOR / "leaflet.css").read_text(encoding="utf-8")
    urls = re.findall(r"url\(([^)]+)\)", text)
    external = [u for u in urls if u.strip("'\" ").startswith(("http://", "https://", "//"))]
    assert not external, f"벤더 CSS 에 외부 URL 이 있다: {external}"


# ── AC-044 : 모바일 우선 · 44px 터치 타깃 · reduced-motion ────────────────
CSS_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _css_blocks(text: str) -> list[tuple[str, str]]:
    return [(sel.strip(), body) for sel, body in CSS_BLOCK_RE.findall(text)]


def _root_variables(text: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for selector, body in _css_blocks(text):
        if ":root" in selector:
            for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body):
                variables[name] = value.strip()
    return variables


def _px(value: str, variables: dict[str, str]) -> float | None:
    value = value.strip()
    var = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if var:
        value = variables.get(var.group(1), "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)px", value.strip())
    return float(match.group(1)) if match else None


def test_stylesheet_declares_mobile_container() -> None:
    text = CSS.read_text(encoding="utf-8")
    assert re.search(r"max-width\s*:\s*560px", text), "560px 콘텐츠 폭이 없다 (NFR-007 · AC-044)"


def test_stylesheet_respects_reduced_motion() -> None:
    text = CSS.read_text(encoding="utf-8")
    assert re.search(r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)", text), (
        "prefers-reduced-motion 규칙이 없다 (NFR-008 · AC-044)"
    )
    tail = text[text.index("prefers-reduced-motion"):]
    assert "animation:none" in tail.replace(" ", ""), "reduced-motion 에서 애니메이션을 끄지 않는다"


# 손가락이 닿는 것들. 원본의 .chk 는 34px 였다 — 여기서 실패하는 것이 정상이었고, 그래서 고쳤다.
INTERACTIVE_SELECTORS = (".chk", ".tab", ".locbtn", ".sortbtn", ".go", ".mini", ".primary", ".secondary")


@pytest.mark.parametrize("selector", INTERACTIVE_SELECTORS)
def test_interactive_targets_are_at_least_44px(selector: str) -> None:
    text = CSS.read_text(encoding="utf-8")
    variables = _root_variables(text)
    blocks = [body for sel, body in _css_blocks(text) if selector in sel.split(",")[0] or f"{selector}{{" in sel]
    blocks = [body for sel, body in _css_blocks(text)
              if any(part.strip().startswith(selector) for part in sel.split(","))]
    assert blocks, f"선택자 {selector} 규칙이 없다"
    merged = " ".join(blocks)
    for prop in ("min-height", "min-width"):
        values = re.findall(rf"{prop}\s*:\s*([^;]+)", merged)
        sizes = [size for size in (_px(v, variables) for v in values) if size is not None]
        assert sizes, f"{selector} 에 {prop} 선언이 없다 (AC-044 · §12 F12 — padding 으로는 보장되지 않는다)"
        assert max(sizes) >= 44, f"{selector} 의 {prop} 이 44px 미만이다: {sizes}"


def test_dark_neon_tokens_are_preserved() -> None:
    """원본의 다크 네온 토큰은 회귀 방지 대상이다 (NFR-009)."""
    variables = _root_variables(CSS.read_text(encoding="utf-8"))
    expected = {
        "--bg": "#0a0e1a", "--pink": "#ff2e88", "--cyan": "#22d3ee",
        "--amber": "#f7b733", "--violet": "#a78bfa", "--ok": "#34d399", "--warn": "#ff6b6b",
    }
    for name, value in expected.items():
        assert variables.get(name, "").lower() == value, f"{name} 토큰이 바뀌었다 (원본: {value})"
