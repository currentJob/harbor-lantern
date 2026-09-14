"""계약 드리프트 검사 — `contracts/openapi.yaml` ↔ `app.openapi()` (설계서 §7.1).

계약을 바꾸려면 **두 곳을 같이** 바꿔야 한다. 그것이 이 파일의 목적이다.

YAML 파서를 쓰지 않는다. `PyYAML` 은 의존성에 없고(NFR-002 — 의존성 하나는 공짜가
아니다), 우리가 계약에서 읽어야 하는 것은 **경로·메서드·상태코드·필수 필드** 넷뿐이다.
그래서 들여쓰기 규칙에 기댄 작은 스캐너를 쓴다 — 계약 파일의 형식이 흐트러지면
`test_contract_file_is_parseable` 이 먼저 실패한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _skip(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def parse_contract_operations(text: str) -> dict[tuple[str, str], set[str]]:
    """`{(메서드, 경로): {상태코드…}}`. `paths:` 블록만 본다."""
    operations: dict[tuple[str, str], set[str]] = {}
    in_paths = False
    path: str | None = None
    method: str | None = None
    in_responses = False

    for line in text.split("\n"):
        if _skip(line):
            continue
        indent = _indent(line)
        stripped = line.strip()

        if indent == 0:
            in_paths = stripped.startswith("paths:")
            path = method = None
            in_responses = False
            continue
        if not in_paths:
            continue
        if indent == 2 and stripped.endswith(":"):
            path = stripped[:-1]
            method = None
            in_responses = False
            continue
        if indent == 4 and stripped[:-1] in METHODS and stripped.endswith(":"):
            method = stripped[:-1]
            in_responses = False
            operations.setdefault((method, str(path)), set())
            continue
        if indent == 6:
            in_responses = stripped == "responses:"
            continue
        if in_responses and indent == 8 and path and method:
            code = stripped.split(":")[0].strip().strip("'\"")
            if code.isdigit():
                operations[(method, path)].add(code)
    return operations


def parse_contract_required(text: str) -> dict[str, list[str]]:
    """`components.schemas.<이름>.required` 만 뽑는다(인라인·여러 줄 둘 다)."""
    lines = [line for line in text.split("\n") if not _skip(line)]
    required: dict[str, list[str]] = {}
    in_schemas = False
    name: str | None = None
    pending: str | None = None

    for line in lines:
        indent = _indent(line)
        stripped = line.strip()
        if indent == 2:
            in_schemas = stripped == "schemas:"
            name = None
            continue
        if not in_schemas:
            continue
        if indent == 4 and stripped.endswith(":"):
            name = stripped[:-1]
            pending = None
            continue
        if name is None:
            continue
        if pending is not None:
            pending += " " + stripped
        elif stripped.startswith("required:"):
            if name in required:
                continue  # 중첩 객체의 required 는 무시한다 (첫 것이 스키마 자신의 것)
            pending = stripped[len("required:") :].strip()
        else:
            continue
        if pending and pending.count("[") and pending.count("[") == pending.count("]"):
            required[name] = [item.strip() for item in pending.strip("[] ").split(",") if item.strip()]
            pending = None
    return required


@pytest.fixture(scope="module")
def contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def contract_operations(contract_text: str) -> dict[tuple[str, str], set[str]]:
    return parse_contract_operations(contract_text)


def implemented_operations(app: Any) -> dict[tuple[str, str], set[str]]:
    spec = app.openapi()
    return {
        (method, path): set(operation.get("responses", {}))
        for path, operations in spec["paths"].items()
        for method, operation in operations.items()
    }


def test_contract_file_is_parseable(contract_operations: dict[tuple[str, str], set[str]]) -> None:
    """스캐너가 계약을 읽어 냈는가. 이것이 깨지면 아래 두 테스트의 통과는 의미가 없다."""
    assert len(contract_operations) >= 18
    assert ("post", "/api/trips") in contract_operations
    assert all(codes for codes in contract_operations.values())


def test_paths_and_methods_match_exactly(app: Any, contract_operations: dict[tuple[str, str], set[str]]) -> None:
    """계약에 있는 오퍼레이션과 구현이 만드는 오퍼레이션이 **정확히 같다**.

    한쪽에만 있는 경로는 둘 중 하나다 — 구현하지 않은 약속이거나, 문서화되지 않은 표면.
    """
    implemented = implemented_operations(app)
    assert set(implemented) == set(contract_operations), (
        f"계약에만 있음: {sorted(set(contract_operations) - set(implemented))} / "
        f"구현에만 있음: {sorted(set(implemented) - set(contract_operations))}"
    )


def test_status_codes_match_exactly(app: Any, contract_operations: dict[tuple[str, str], set[str]]) -> None:
    """오퍼레이션마다 상태코드 집합이 같다(계약이 적지 않은 코드를 구현이 내면 실패)."""
    implemented = implemented_operations(app)
    mismatched = {
        key: (sorted(contract_operations[key]), sorted(implemented[key]))
        for key in contract_operations
        if key in implemented and contract_operations[key] != implemented[key]
    }
    assert not mismatched, f"상태코드 불일치 (계약, 구현): {mismatched}"


def test_documented_error_shape_is_what_the_server_sends(trip: Any) -> None:
    """계약의 `Error` 스키마(= `{error, message, detail?}`)가 실제 본문과 같다."""
    missing = trip.client.delete(f"{trip.base}/spots/없는스팟", headers=trip.headers)
    assert missing.status_code == 404
    assert set(missing.json()) == {"error", "message"}

    conflict = trip.client.put(
        f"{trip.base}/days/1/order",
        headers=trip.headers,
        json={"expected_revision": trip.revision() + 5, "spot_ids": trip.spot_ids(1)},
    )
    assert conflict.status_code == 409
    assert set(conflict.json()) == {"error", "message", "detail"}
    assert set(conflict.json()["detail"]) == {"current_revision"}


def test_required_response_fields_are_present(trip: Any, client: Any, contract_text: str, fx_port: Any) -> None:
    """계약이 `required` 로 못박은 필드가 **실제 응답에** 전부 있다.

    스키마 이름만 맞춰 놓고 필드가 빠지면 프론트는 `undefined` 를 화면에 그린다 —
    그것도 조용히.
    """
    required = parse_contract_required(contract_text)
    state = trip.state().json()
    spot = state["days"][0]["spots"][0]
    created = trip.client.post(
        f"{trip.base}/expenses",
        headers=trip.headers,
        json={
            "payer_id": trip.owner.participant_id,
            "amount_minor": 1_000,
            "share_participant_ids": [trip.owner.participant_id],
        },
    ).json()

    samples: dict[str, dict[str, Any]] = {
        "Trip": state["trip"],
        "Participant": state["participants"][0],
        "Progress": state["progress"],
        "TripState": state,
        "DayState": state["days"][0],
        "Spot": spot,
        "DoneState": spot["done"],
        "HoursSpec": spot["hours"],
        "Schedule": spot["schedule"],
        "Leg": spot["leg_to_next"],
        "Expense": created,
        "ExpenseList": trip.client.get(f"{trip.base}/expenses", headers=trip.headers).json(),
        "Settlement": trip.client.get(f"{trip.base}/settlement", headers=trip.headers).json(),
        "DayOrderResponse": trip.client.put(
            f"{trip.base}/days/1/order",
            headers=trip.headers,
            json={"expected_revision": trip.revision(), "spot_ids": trip.spot_ids(1)},
        ).json(),
        "RouteProposal": trip.client.post(f"{trip.base}/days/2/optimize", headers=trip.headers).json(),
        "ExternalMeta": client.get("/api/fx").json(),
    }

    for name, body in samples.items():
        assert name in required, f"계약에 {name}.required 가 없다 (스캐너 또는 계약 문제)"
        missing = [field for field in required[name] if field not in body]
        assert not missing, f"{name} 응답에 계약 필수 필드가 없다: {missing}"


def test_contract_documents_no_unused_error_codes(contract_text: str) -> None:
    """계약이 참조하는 공통 응답(`#/components/responses/...`)이 실제로 정의돼 있다."""
    referenced = set(re.findall(r"#/components/responses/(\w+)", contract_text))
    defined = set(re.findall(r"^    (\w+):$", contract_text, flags=re.MULTILINE))
    assert referenced <= defined, f"정의되지 않은 공통 응답 참조: {sorted(referenced - defined)}"
