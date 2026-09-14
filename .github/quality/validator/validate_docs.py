#!/usr/bin/env python3
"""구조 검증기 (Structural Validator) — 결정론적 hard gate.

`.ai/schemas/*.json` 계약에 따라 프로젝트의 `docs/` 표준 문서 세트가
구조·ID 형식·추적성(REQ↔DSN↔TC↔AC)을 지키는지 검증한다.

- 결정론적이므로 자동 차단(hard gate)해도 안전하다.
- 의미(Semantic) 검증은 이 스크립트의 책임이 아니다(거버넌스 문서 §4 참조):
  의미 판단은 LLM/사람이 confidence-score + flag로 다루고 human review로 넘긴다.

사용:
    python validate_docs.py <docs_dir>
예:
    python .ai/validator/validate_docs.py web_2048/docs

종료 코드: 0 = 계약 준수 / 1 = 위반(에러) 존재.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ID_KINDS = ["REQ", "NFR", "AC", "DSN", "TC"]
SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = SCRIPT_DIR.parent / "schemas"


def load_schemas() -> dict[str, dict]:
    schemas: dict[str, dict] = {}
    for f in sorted(SCHEMA_DIR.glob("*.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        schemas[s["doc_id"]] = s
    return schemas


def find_doc(docs_dir: Path, doc_id: str) -> Path | None:
    for f in sorted(docs_dir.glob(f"{doc_id}_*.md")):
        return f
    return None


def expand_ids(text: str, kind: str) -> set[str]:
    """텍스트에서 KIND-NNN ID를 수집. `KIND-001~003` 범위 표기도 전개한다.

    계약 규정: ID는 반드시 접두어를 포함한 정식 형태로 쓴다.
    (`REQ-002, 004` 같은 접두어 생략 축약은 파싱되지 않아 드리프트로 간주된다.)
    """
    ids: set[str] = set()
    # 범위: KIND-001~003  또는 KIND-001~KIND-003
    for m in re.finditer(rf"{kind}-(\d+)\s*~\s*(?:{kind}-)?(\d+)", text):
        a, b, width = int(m.group(1)), int(m.group(2)), len(m.group(1))
        if a <= b and (b - a) < 200:
            for n in range(a, b + 1):
                ids.add(f"{kind}-{str(n).zfill(width)}")
    # 단일
    for m in re.finditer(rf"{kind}-(\d+)", text):
        ids.add(f"{kind}-{m.group(1)}")
    return ids


def check_structural(schema: dict, text: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for meta in schema.get("required_meta", []):
        if meta not in text:
            errors.append(f"필수 메타 항목 '{meta}' 누락")

    if schema.get("requires_changelog") and not re.search(r"개정\s*이력", text):
        errors.append("개정이력 섹션 누락")

    headings = "\n".join(re.findall(r"^#{1,6}\s+(.*)$", text, re.M))
    for sec in schema.get("required_sections", []):
        if sec not in headings and sec not in text:
            errors.append(f"필수 섹션 '{sec}' 누락")

    for kind in schema.get("defines_ids", []):
        if not expand_ids(text, kind):
            errors.append(f"{kind} ID가 하나도 정의되지 않음")

    # 비표준 ID 형식(1자리) 감지 — 드리프트 조기 경고
    for kind in ID_KINDS:
        for m in re.finditer(rf"{kind}-(\d)(?!\d)", text):
            warnings.append(f"비표준 ID 형식 '{kind}-{m.group(1)}' (2자리 이상 권장)")

    return errors, warnings


def check_traceability(texts: dict[str, str]) -> tuple[list[str], list[str]]:
    """REQ↔DSN↔TC↔AC 교차 추적성 검증 (결정론적)."""
    errors: list[str] = []
    warnings: list[str] = []
    t = {k: texts.get(k, "") for k in ["00", "01", "02", "03", "04", "05"]}

    req01 = expand_ids(t["01"], "REQ")
    nfr01 = expand_ids(t["01"], "NFR")
    ac01 = expand_ids(t["01"], "AC")
    req00 = expand_ids(t["00"], "REQ")
    nfr00 = expand_ids(t["00"], "NFR")

    # R1: 요구사항 ↔ 추적성 매트릭스(00) 일치 (고아/유령 금지)
    for r in sorted(req01 - req00):
        errors.append(f"[R1] {r} 이(가) 01에 정의됐으나 추적성 매트릭스(00)에 없음 (고아 REQ)")
    for r in sorted(req00 - req01):
        errors.append(f"[R1] {r} 이(가) 매트릭스(00)에 있으나 01에 미정의 (유령 REQ)")
    for n in sorted(nfr01 - nfr00):
        warnings.append(f"[R1] {n} 이(가) 추적성 매트릭스(00)에 누락")

    # R2: 설계(02)가 모든 REQ를 다루는가
    for r in sorted(req01):
        if r not in t["02"]:
            errors.append(f"[R2] {r} 이(가) 설계서(02)에서 참조되지 않음 (미설계 요구사항)")
    for n in sorted(nfr01):
        if n not in t["02"]:
            warnings.append(f"[R2] {n} 이(가) 설계서(02)에서 참조되지 않음")

    # R3: 테스트(04)/매트릭스(00)가 모든 AC를 커버하는가
    ac_covered = expand_ids(t["04"], "AC") | expand_ids(t["00"], "AC")
    for a in sorted(ac01 - ac_covered):
        errors.append(f"[R3] {a} 이(가) 테스트결과서(04)/매트릭스(00)에서 커버되지 않음 (미검증 AC)")

    # R4: 참조된 DSN/TC가 실제로 정의됐는가
    dsn_def = expand_ids(t["02"], "DSN")
    dsn_ref = expand_ids(t["00"], "DSN") | expand_ids(t["03"], "DSN") | expand_ids(t["04"], "DSN")
    for d in sorted(dsn_ref - dsn_def):
        errors.append(f"[R4] {d} 이(가) 참조됐으나 설계서(02)에 미정의 (유령 DSN)")
    tc_def = expand_ids(t["04"], "TC")
    tc_ref = expand_ids(t["00"], "TC")
    for c in sorted(tc_ref - tc_def):
        errors.append(f"[R4] {c} 이(가) 참조됐으나 테스트결과서(04)에 미정의 (유령 TC)")

    return errors, warnings


def main(argv: list[str]) -> int:
    # Windows 콘솔(cp949) 등에서도 기호 출력이 깨지지 않도록 UTF-8 고정
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if len(argv) != 2:
        print("사용법: python validate_docs.py <docs_dir>")
        return 2
    docs_dir = Path(argv[1]).resolve()
    if not docs_dir.is_dir():
        print(f"오류: docs 디렉토리를 찾을 수 없음: {docs_dir}")
        return 2

    schemas = load_schemas()
    texts: dict[str, str] = {}
    total_errors = 0
    total_warnings = 0

    print(f"■ 문서 계약 검증: {docs_dir}")
    print("─" * 60)

    # 1) 문서별 구조 검증
    for doc_id in sorted(schemas):
        schema = schemas[doc_id]
        path = find_doc(docs_dir, doc_id)
        if path is None:
            if schema.get("required", False):
                print(f"[{doc_id}] ✗ 필수 문서 누락 ({schema['title']})")
                total_errors += 1
            continue
        text = path.read_text(encoding="utf-8")
        texts[doc_id] = text
        errors, warnings = check_structural(schema, text)
        status = "✓" if not errors else "✗"
        print(f"[{doc_id}] {status} {path.name}")
        for e in errors:
            print(f"      ✗ {e}")
        for w in warnings:
            print(f"      ⚠ {w}")
        total_errors += len(errors)
        total_warnings += len(warnings)

    # 2) 교차 추적성 검증
    print("─" * 60)
    print("■ 추적성 검증 (REQ↔DSN↔TC↔AC)")
    terrors, twarnings = check_traceability(texts)
    if not terrors and not twarnings:
        print("      ✓ 고아/유령/미커버 ID 없음")
    for e in terrors:
        print(f"      ✗ {e}")
    for w in twarnings:
        print(f"      ⚠ {w}")
    total_errors += len(terrors)
    total_warnings += len(twarnings)

    print("─" * 60)
    print(f"결과: 에러 {total_errors} / 경고 {total_warnings}")
    if total_errors:
        print("판정: ✗ 계약 위반 — 반영 차단 (hard gate)")
        return 1
    print("판정: ✓ 계약 준수")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
