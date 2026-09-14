#!/usr/bin/env python3
"""의존성 라이선스 준수 검사 (release-engineering.md §3).

**생성된 파일이다. 프로젝트에서 직접 고치지 말 것** — 하네스가 빌드마다 덮어쓴다.
원본: `.claude/skills/product-build-orchestrator/assets/scripts/check_licenses.py`

SBOM(CycloneDX)을 읽어 의존성의 라이선스를 훑고, **가져다 쓰면 내 코드까지 조건이 옮는**
라이선스가 섞여 있는지 본다. 이건 보안이 아니라 법무 문제라 빌드가 깨지지 않으면 아무도
모르고 지나간다 — 그래서 CI 게이트에 둔다.

정책:
- **차단(DENY)**: 강한 카피레프트·비상업 조건. 배포물에 섞이면 소스 공개나 사용 제한이 따라온다.
- **경고(REVIEW)**: 약한 카피레프트. 정적 링크 여부에 따라 달라지므로 사람이 판단한다.
- **미상(UNKNOWN)**: 라이선스를 못 읽은 항목. 개수만 보고한다 — 스캐너가 못 읽었을 뿐인
  경우가 많아 이걸로 빌드를 깨면 게이트가 신뢰를 잃는다.

예외를 두려면 프로젝트 루트에 `.license-allow` 파일을 만들고 `이름@버전` 을 한 줄씩 적는다
(사유를 주석 `#` 으로 함께 남길 것).

종료 코드: 0 = 통과 / 1 = 차단 라이선스 발견.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DENY = [
    "AGPL", "SSPL", "BUSL", "CC-BY-NC", "CC-BY-ND", "CPAL",
    "OSL-3.0", "EUPL", "RPL-1.5", "Commons-Clause", "Elastic-2.0",
]
REVIEW = ["GPL", "LGPL", "MPL", "CDDL", "EPL", "CECILL"]

# "GPL" 이 "LGPL"·"AGPL" 안에 들어 있으므로 경계를 잡아 오탐을 막는다.
def _matches(license_id: str, needles: list[str]) -> str | None:
    upper = license_id.upper()
    for n in needles:
        nu = n.upper()
        if re.search(rf"(^|[^A-Z]){re.escape(nu)}([^A-Z]|$)", upper):
            return n
    return None


def licenses_of(component: dict) -> list[str]:
    out: list[str] = []
    for entry in component.get("licenses", []) or []:
        lic = entry.get("license") or {}
        value = lic.get("id") or lic.get("name") or entry.get("expression")
        if value:
            out.append(str(value))
    return out


def load_allowlist(root: Path) -> set[str]:
    f = root / ".license-allow"
    if not f.is_file():
        return set()
    allow = set()
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            allow.add(line)
    return allow


def _force_utf8() -> None:
    """Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않게 한다(validate_docs.py 와 동일 처리)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str]) -> int:
    _force_utf8()
    if len(argv) < 2:
        print("사용법: python check_licenses.py <sbom.cyclonedx.json>")
        return 2
    sbom_path = Path(argv[1])
    if not sbom_path.is_file():
        print(f"오류: SBOM 을 찾을 수 없음: {sbom_path}")
        return 2

    try:
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"오류: SBOM 파싱 실패 — {e}")
        return 2

    allow = load_allowlist(sbom_path.resolve().parent)
    denied: list[str] = []
    review: list[str] = []
    unknown = 0
    total = 0

    for c in sbom.get("components", []) or []:
        total += 1
        name = c.get("name", "?")
        version = c.get("version", "?")
        key = f"{name}@{version}"
        ids = licenses_of(c)
        if not ids:
            unknown += 1
            continue
        if key in allow:
            continue
        for lic in ids:
            if hit := _matches(lic, DENY):
                denied.append(f"{key} — {lic} (차단: {hit})")
            elif hit := _matches(lic, REVIEW):
                review.append(f"{key} — {lic} (검토: {hit})")

    print(f"■ 의존성 라이선스 검사: 컴포넌트 {total}건")
    print("-" * 60)
    for r in sorted(set(review)):
        print(f"  ~   {r}")
    for d in sorted(set(denied)):
        print(f"  !!  {d}")
    if unknown:
        print(f"  ?   라이선스 미상 {unknown}건 (스캐너가 읽지 못함 — 차단하지 않음)")
    if allow:
        print(f"  --  예외 허용 {len(allow)}건 (.license-allow)")
    print("-" * 60)

    if denied:
        print(f"결과: 차단 라이선스 {len(set(denied))}건 — 의존성을 교체하거나")
        print("      정당한 사유가 있으면 .license-allow 에 사유와 함께 등록하세요.")
        return 1
    print(f"결과: 차단 0건 / 검토 필요 {len(set(review))}건")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
