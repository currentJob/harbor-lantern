"""CI · 릴리스 워크플로 계약 — AC-049 (NFR-016).

Phase 4 의 AC 대조에서 드러난 구멍: `.github/workflows/` 를 읽는 테스트가 하나도 없었다.
`tests/static/test_secrets_and_pinning.py` 는 LICENSE(AC-045)와 의존성 고정(AC-041)까지만 보고,
**"CI 에 그 검사들이 정의돼 있는가"** 는 아무도 묻지 않았다.

그 구멍이 위험한 이유는 워크플로가 **조용히 사라져도 로컬 스위트가 전부 통과하기 때문**이다.
누가 느린 잡 하나를 주석 처리하고 되돌리는 것을 잊으면, 그 뒤로 시크릿 스캔 없는 커밋이
계속 green 을 받는다. 그리고 그 사실은 비밀이 새고 나서야 알려진다.

AC-049 가 요구하는 것은 두 문장이다.
1. CI 워크플로에 **LICENSE 검사 · 버전 고정 검사 · 시크릿 스캔 · 취약점 스캔 · SAST ·
   SBOM 생성 · 문서 계약 검증 · 테스트** 단계가 모두 정의돼 있다.
2. 릴리스 워크플로가 **태그 트리거**로 **CI 재검증** 후 **산출물과 SBOM** 을 첨부한다.

여기서는 워크플로를 **구조로**(YAML 트리) 읽는다. 문자열 grep 은 주석에 적힌 단어에도
걸려서 "주석만 남고 단계는 사라진" 상태를 통과시킨다 — 그것이 정확히 이 검사가 막으려는 것이다.

`yaml` 은 `uvicorn[standard]` 가 끌어오는 전이 의존이며 `uv.lock` 에 6.0.3 으로 고정돼 있다
(NFR-002 — 떠 있는 버전이 아니다).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"


def _load(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"워크플로가 없다: {path.relative_to(PROJECT_ROOT)} (AC-049)"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path.name} 이 매핑이 아니다"
    return document


def _triggers(document: dict[str, Any]) -> dict[str, Any]:
    """YAML 1.1 은 따옴표 없는 `on` 을 **불리언 True 로 읽는다.** 두 키를 모두 본다."""
    raw = document.get("on", document.get(True))
    assert raw is not None, "트리거(`on`) 가 없다"
    return raw if isinstance(raw, dict) else {key: None for key in ([raw] if isinstance(raw, str) else raw)}


def _steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in document.get("jobs", {}).values():
        if isinstance(job, dict):
            steps += [step for step in job.get("steps", []) or [] if isinstance(step, dict)]
    return steps


def _blob(steps: list[dict[str, Any]]) -> str:
    """단계의 `uses`·`run`·`with` 만 모은다 — **`name` 과 주석은 일부러 뺀다.**

    이름은 사람이 붙이는 라벨이라 검사의 근거가 될 수 없다. "시크릿 스캔"이라고 이름 붙은
    단계가 `echo ok` 를 돌려도 이름만 보는 검사는 통과시킨다.
    """
    parts: list[str] = []
    for step in steps:
        parts.append(str(step.get("uses", "")))
        parts.append(str(step.get("run", "")))
        parts.append(str(step.get("with", "")))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return _load(CI)


@pytest.fixture(scope="module")
def release() -> dict[str, Any]:
    return _load(RELEASE)


# ── AC-049 전단 : CI 게이트 8종 ───────────────────────────────────────────
def test_ac049_ci_runs_on_push_and_pull_request(ci: dict[str, Any]) -> None:
    """게이트가 PR 에서 안 돌면 게이트가 아니다 — 머지된 뒤에 아는 것은 늦다."""
    triggers = _triggers(ci)
    assert "push" in triggers and "pull_request" in triggers, f"트리거가 부족하다: {sorted(triggers)}"
    assert "workflow_call" in triggers, "release.yml 이 재사용할 수 없다 (게이트가 두 벌이 된다)"


def test_ci_has_jobs_and_steps(ci: dict[str, Any]) -> None:
    """아래 모든 검사의 전제 — 단계가 0건이면 전부 '항상 통과'가 된다."""
    assert ci.get("jobs"), "잡이 하나도 없다"
    assert len(_steps(ci)) >= 8, f"단계가 너무 적다 ({len(_steps(ci))}건)"


# (단계 이름, 그 단계를 식별하는 정규식) — AC-049 가 열거한 8종 그대로다.
CI_REQUIRED_STAGES = [
    ("LICENSE 검사", r"\btest -[fe] LICENSE\b|check_licen|LICENSE.*\bexit 1\b"),
    ("버전 고정 검사", r"check_pinning\.py"),
    ("시크릿 스캔", r"gitleaks"),
    ("취약점 스캔", r"osv-scanner|osv_scanner|trivy|grype"),
    ("SAST", r"semgrep"),
    ("SBOM 생성", r"sbom-action|syft|cyclonedx"),
    ("문서 계약 검증", r"check_documents\.py|validate_docs\.py"),
    ("빌드·테스트", r"\bpytest\b"),
]


@pytest.mark.parametrize(("stage", "pattern"), CI_REQUIRED_STAGES, ids=[s for s, _ in CI_REQUIRED_STAGES])
def test_ac049_ci_defines_every_required_stage(ci: dict[str, Any], stage: str, pattern: str) -> None:
    """AC-049: 8종 게이트가 **실행되는 단계로** 정의돼 있다(이름표가 아니라 `uses`/`run` 으로)."""
    assert re.search(pattern, _blob(_steps(ci)), re.IGNORECASE), f"CI 에 '{stage}' 단계가 없다 (AC-049)"


def test_ac049_dependency_license_check_is_wired_to_the_sbom(ci: dict[str, Any]) -> None:
    """AC-049 파생: 의존성 라이선스 검사가 SBOM 산출물을 입력으로 받는다.

    두 단계가 각자 돌면 SBOM 이 비어도 라이선스 검사는 조용히 통과한다.
    """
    blob = _blob(_steps(ci))
    assert "check_licenses.py" in blob, "의존성 라이선스 검사가 없다"
    assert re.search(r"check_licenses\.py\s+\S*sbom\S*\.json", blob), "라이선스 검사가 SBOM 을 읽지 않는다"


def test_ci_actions_are_version_pinned(ci: dict[str, Any]) -> None:
    """NFR-002 — CI 가 스스로 지키지 않는 규칙을 프로젝트에 요구할 수는 없다."""
    floating = [
        step["uses"]
        for step in _steps(ci)
        if step.get("uses") and not step["uses"].startswith("./") and "@" not in step["uses"].split("/")[-1]
    ]
    assert not floating, f"버전이 고정되지 않은 액션이 있다: {floating}"


def test_ci_permissions_start_read_only(ci: dict[str, Any]) -> None:
    """최소 권한 — 게이트는 읽기만 하면 된다."""
    assert ci.get("permissions", {}).get("contents") == "read"


# ── AC-049 후단 : 릴리스 워크플로 ─────────────────────────────────────────
def test_ac049_release_is_triggered_by_a_version_tag(release: dict[str, Any]) -> None:
    """AC-049: 릴리스는 **태그 트리거**다(수동 버튼도, 브랜치 푸시도 아니다)."""
    triggers = _triggers(release)
    assert "push" in triggers, f"태그 푸시 트리거가 없다: {sorted(triggers)}"
    tags = (triggers["push"] or {}).get("tags")
    assert tags, "`on.push.tags` 가 없다 — 브랜치 푸시마다 릴리스가 나간다"
    assert any(pattern.startswith("v") for pattern in tags), f"SemVer 태그 패턴이 아니다: {tags}"


def test_ac049_release_reverifies_through_the_ci_gate(release: dict[str, Any]) -> None:
    """AC-049: 배포 전에 **CI 를 그대로 재실행**하고, 통과한 경우에만 다음 잡이 돈다."""
    jobs = release.get("jobs", {})
    reusing = {name: job for name, job in jobs.items() if str(job.get("uses", "")).endswith("/ci.yml")}
    assert reusing, f"CI 워크플로를 재사용하는 잡이 없다 (게이트가 두 벌이 된다): {sorted(jobs)}"

    verify_job = next(iter(reusing))
    publishing = [
        name for name, job in jobs.items() if name not in reusing and "softprops/action-gh-release" in str(job)
    ]
    assert publishing, "릴리스를 만드는 잡이 없다"
    for name in publishing:
        needs = jobs[name].get("needs")
        needs = [needs] if isinstance(needs, str) else list(needs or [])
        assert verify_job in needs, f"'{name}' 잡이 검증('{verify_job}')을 기다리지 않는다 (AC-049)"


def test_ac049_release_attaches_artifacts_and_the_sbom(release: dict[str, Any]) -> None:
    """AC-049: 릴리스에 **산출물과 SBOM** 이 첨부된다."""
    steps = _steps(release)
    blob = _blob(steps)
    assert re.search(r"sbom-action|syft|cyclonedx", blob, re.IGNORECASE), "SBOM 을 만들지 않는다"

    attach = [step for step in steps if "softprops/action-gh-release" in str(step.get("uses", ""))]
    assert attach, "릴리스 첨부 단계가 없다"
    files = str(attach[0].get("with", {}).get("files", ""))
    assert "sbom" in files.lower(), f"SBOM 이 첨부 목록에 없다: {files!r}"
    assert "SHA256SUMS" in files, f"체크섬이 첨부 목록에 없다: {files!r}"


def test_release_runs_the_test_suite_before_publishing(release: dict[str, Any]) -> None:
    """AC-049 파생: 재검증 잡과 별개로 배포 잡 자신도 테스트를 돌린다."""
    assert re.search(r"\bpytest\b", _blob(_steps(release))), "릴리스 잡이 테스트를 돌리지 않는다"


def test_release_only_escalates_permissions_where_it_publishes(release: dict[str, Any]) -> None:
    """`contents: write` 는 릴리스를 만드는 잡에만 있어야 한다."""
    assert release.get("permissions", {}).get("contents") == "read", "워크플로 기본 권한이 읽기가 아니다"
    writers = [
        name for name, job in release.get("jobs", {}).items()
        if (job.get("permissions") or {}).get("contents") == "write"
    ]
    assert len(writers) == 1, f"쓰기 권한을 가진 잡이 {len(writers)}개다: {writers}"


def test_release_actions_are_version_pinned(release: dict[str, Any]) -> None:
    floating = [
        step["uses"]
        for step in _steps(release)
        if step.get("uses") and not step["uses"].startswith("./") and "@" not in step["uses"].split("/")[-1]
    ]
    assert not floating, f"버전이 고정되지 않은 액션이 있다: {floating}"


# ── 결함 D-1 (해결됨 2026-09-14) ───────────────────────────────────────────
# 이 테스트는 xfail(strict) 로 결함을 고정해 두었다가, `release.yml` 에 `uv build` 단계가
# 들어오면서 XPASS 로 그 사실을 알렸다. 표식을 걷어 일반 회귀 테스트로 승격한다 —
# 첨부 목록에 있는데 아무도 만들지 않는 경로가 다시 생기면 여기서 잡힌다.
def test_release_attachment_globs_have_a_step_that_produces_them(release: dict[str, Any]) -> None:
    """`fail_on_unmatched_files: true` 인데 아무도 만들지 않는 경로가 첨부 목록에 있으면 릴리스가 깨진다.

    이 테스트는 **지금 실패하는 것이 정상이다** — `xfail(strict=True)` 로 결함 D-1 을 고정해 둔다.
    고쳐지면 `strict` 가 XPASS 로 알려 주므로, 고치고 나서 이 표시를 떼면 된다.
    자세한 내용은 `docs/04_테스트결과서.md` §결함 D-1.
    """
    steps = _steps(release)
    attach = next(step for step in steps if "softprops/action-gh-release" in str(step.get("uses", "")))
    options = attach.get("with", {})
    if not options.get("fail_on_unmatched_files"):
        pytest.skip("fail_on_unmatched_files 가 꺼져 있다 — 빈 글롭이 릴리스를 깨뜨리지 않는다")

    patterns = [line.strip() for line in str(options.get("files", "")).splitlines() if line.strip()]
    produced = _blob(steps) + "\n".join(str(step.get("run", "")) for step in steps)
    unproduced = []
    for pattern in patterns:
        root = pattern.split("/")[0].split("*")[0]
        if not root or root in {"SHA256SUMS"} or "sbom" in root.lower():
            continue  # 체크섬·SBOM 은 앞 단계가 만든다
        if (PROJECT_ROOT / root).exists():
            continue
        if re.search(rf"\b{re.escape(root)}\b", produced) and not re.search(
            rf"(uv build|python -m build|mkdir[^\n]*{re.escape(root)}|cp[^\n]*{re.escape(root)})", produced
        ):
            unproduced.append(pattern)
        elif not re.search(rf"\b{re.escape(root)}\b", produced):
            unproduced.append(pattern)

    assert not unproduced, (
        "릴리스 첨부 목록에 **아무 단계도 만들지 않는** 경로가 있다 — 태그를 밀면 첨부 단계에서 실패한다 "
        f"(결함 D-1): {unproduced}"
    )
