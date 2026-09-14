#!/usr/bin/env python
"""Leaflet 벤더 자산 무결성 검사 (설계서 DSN-22 · §12 F11 · AC-042).

프론트는 CDN 을 쓰지 않는다(NFR-015). 그래서 Leaflet 은 저장소 안의 파일이고,
**저장소 안의 파일은 조용히 바뀐다** — 편집기의 자동 포매팅, git 의 CRLF 변환,
"버전만 살짝" 올린 수동 교체. 그중 어느 것도 화면에서는 안 보인다.
개발자 브라우저에는 CDN 캐시가 남아 지도가 멀쩡히 뜨기 때문이다.

그래서 바이트 단위 해시를 매니페스트에 적어 두고 게이트에서 대조한다.

    python tools/vendor_check.py            # 검증 (불일치 → 종료코드 1)
    python tools/vendor_check.py --update   # 매니페스트 재생성 (의도적 교체 후에만)

매니페스트 형식은 `sha256sum` 과 동일한 `<hex>  <상대경로>` 라서,
`cd .../leaflet-1.9.4 && sha256sum -c MANIFEST.sha256` 로도 확인할 수 있다.

출처(조회일 2026-09-13): npm registry `leaflet@1.9.4` 타르볼
`https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz`
— 레지스트리 메타데이터의 `dist.integrity`
`sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==`
및 `dist.shasum` `23fae724e282fa25745aff82ca4d394748db7d8d` 와 대조해 확인한 파일이다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = PROJECT_ROOT / "src" / "harbor_lantern" / "web" / "vendor" / "leaflet-1.9.4"
MANIFEST = VENDOR_DIR / "MANIFEST.sha256"

# 매니페스트가 담아야 하는 파일 목록. 여기 없는 파일이 벤더 디렉터리에 있으면
# "출처를 모르는 파일"이므로 실패시킨다 — 벤더링의 요점은 목록이 닫혀 있는 것이다.
EXPECTED_FILES = (
    "images/layers-2x.png",
    "images/layers.png",
    "images/marker-icon-2x.png",
    "images/marker-icon.png",
    "images/marker-shadow.png",
    "leaflet.css",
    "leaflet.js",
)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actual_files(vendor_dir: Path) -> list[str]:
    return sorted(
        p.relative_to(vendor_dir).as_posix()
        for p in vendor_dir.rglob("*")
        if p.is_file() and p.name != MANIFEST.name
    )


def parse_manifest(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        digest, _, name = stripped.partition("  ")
        if not name:
            raise ValueError(f"매니페스트 형식 오류: {line!r} (기대: '<hex>  <경로>')")
        entries[name.lstrip("*").strip()] = digest.strip()
    return entries


def render_manifest(vendor_dir: Path, names: list[str]) -> str:
    return "".join(f"{sha256_of(vendor_dir / name)}  {name}\n" for name in names)


def check(vendor_dir: Path = VENDOR_DIR, manifest: Path = MANIFEST) -> list[str]:
    """문제 목록을 돌려준다. 빈 목록이면 통과."""
    problems: list[str] = []
    if not vendor_dir.is_dir():
        return [f"벤더 디렉터리가 없다: {vendor_dir}"]
    if not manifest.is_file():
        return [f"매니페스트가 없다: {manifest} (`--update` 로 생성)"]

    recorded = parse_manifest(manifest.read_text(encoding="utf-8"))
    present = actual_files(vendor_dir)

    for name in EXPECTED_FILES:
        if name not in present:
            problems.append(f"필수 벤더 파일 누락: {name}")
        if name not in recorded:
            problems.append(f"매니페스트에 항목 없음: {name}")

    for name in present:
        if name not in recorded:
            problems.append(f"매니페스트에 없는 파일이 벤더 디렉터리에 있다: {name}")

    for name, digest in sorted(recorded.items()):
        path = vendor_dir / name
        if not path.is_file():
            problems.append(f"매니페스트에 적힌 파일이 없다: {name}")
            continue
        found = sha256_of(path)
        if found != digest:
            problems.append(f"해시 불일치: {name}\n    기대 {digest}\n    실제 {found}")
    return problems


def update(vendor_dir: Path = VENDOR_DIR, manifest: Path = MANIFEST) -> int:
    names = actual_files(vendor_dir)
    missing = [name for name in EXPECTED_FILES if name not in names]
    if missing:
        print("갱신 거부 — 필수 파일이 없다: " + ", ".join(missing), file=sys.stderr)
        return 1
    manifest.write_text(render_manifest(vendor_dir, names), encoding="utf-8", newline="\n")
    print(f"매니페스트 갱신: {manifest.relative_to(PROJECT_ROOT)} ({len(names)}개 파일)")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows 기본 콘솔 인코딩(cp949)은 '—' 같은 문자에서 UnicodeEncodeError 로 죽는다.
    # 검사기가 검사 결과 대신 인코딩 예외를 뱉으면 아무도 결과를 못 본다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Leaflet 벤더 자산 SHA256 무결성 검사")
    parser.add_argument("--update", action="store_true", help="매니페스트를 현재 파일 기준으로 재생성한다")
    args = parser.parse_args(argv)

    if args.update:
        return update()

    problems = check()
    if problems:
        print("벤더 무결성 검사 실패:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"벤더 무결성 OK — {len(EXPECTED_FILES)}개 파일 (leaflet 1.9.4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
