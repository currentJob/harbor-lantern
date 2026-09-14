"""경비 기록 · 원화 환산 · 최소 송금 정산 — DSN-12 · DSN-13 (설계서 §6.20 · NFR-014).

금액은 전부 **정수 minor unit**(HKD cent)이다. 분담액은 **기록 시점에 확정 저장**하고,
정산은 저장된 값을 그대로 더한다 — 나중에 참가자가 추가돼도 과거 지출의 분담이 흔들리지
않고, 재계산 드리프트가 원천적으로 없다.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from harbor_lantern.api.errors import NotFoundError, UnprocessableError, VersionConflictError
from harbor_lantern.clock import Clock
from harbor_lantern.domain.money import hkd_cent_to_krw, split_even
from harbor_lantern.domain.settle import balances as compute_balances
from harbor_lantern.domain.settle import settle
from harbor_lantern.domain.util import format_iso_utc, parse_iso_utc
from harbor_lantern.storage import repo_expenses, repo_spots, repo_trips
from harbor_lantern.storage.db import transaction

__all__ = [
    "CURRENCY",
    "build_settlement",
    "create_expense",
    "delete_expense",
    "expense_payload",
    "list_expenses",
    "update_expense",
]

CURRENCY = "HKD"


def _krw(amount_minor: int, rate_micro: int | None) -> int | None:
    """환율이 없으면 **null 이고 원금은 정상 반환된다**(AC-029). 예외를 던지지 않는다."""
    return None if rate_micro is None else hkd_cent_to_krw(amount_minor, rate_micro)


def expense_payload(
    row: sqlite3.Row,
    shares: Mapping[str, int],
    rate_micro: int | None,
) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "payer_id": str(row["payer_id"]),
        "amount_minor": int(row["amount_minor"]),
        "amount_krw": _krw(int(row["amount_minor"]), rate_micro),
        "currency": str(row["currency"]),
        "note": str(row["note"]),
        "spot_id": row["spot_id"],
        "spent_at": str(row["spent_at"]),
        "version": int(row["version"]),
        "shares": [
            {"participant_id": participant_id, "share_minor": share_minor}
            for participant_id, share_minor in sorted(shares.items())
        ],
    }


def list_expenses(
    conn: sqlite3.Connection,
    trip_id: str,
    *,
    rate_micro: int | None,
    fx_payload: Mapping[str, Any],
) -> dict[str, Any]:
    rows = repo_expenses.list_expenses(conn, trip_id)
    shares = repo_expenses.list_shares(conn, trip_id)
    items = [expense_payload(row, shares.get(str(row["id"]), {}), rate_micro) for row in rows]
    total_minor = sum(int(row["amount_minor"]) for row in rows)
    return {
        "items": items,
        "total_minor": total_minor,
        "total_krw": _krw(total_minor, rate_micro),
        "currency": CURRENCY,
        "fx": dict(fx_payload),
    }


def create_expense(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    trip: sqlite3.Row,
    payer_id: str,
    amount_minor: int,
    note: str,
    spot_id: str | None,
    spent_at: str | None,
    share_participant_ids: Sequence[str],
    rate_micro: int | None,
) -> dict[str, Any]:
    """경비 1건 + 분담 n건 + 리비전 +1 을 한 트랜잭션에서 (REQ-007 · AC-012 · AC-013)."""
    trip_id = str(trip["id"])
    members = {str(row["id"]) for row in repo_trips.list_participants(conn, trip_id)}
    _require_member(payer_id, members, "payer_id")
    _require_share_ids(share_participant_ids, members)
    if spot_id is not None and repo_spots.get_spot(conn, trip_id, spot_id) is None:
        raise UnprocessableError("해당 여행의 스팟이 아닙니다.", code="unknown_spot")

    now = format_iso_utc(clock.now_utc())
    when = now if spent_at is None else _normalize_instant(spent_at)
    expense_id = uuid.uuid4().hex
    shares = split_even(amount_minor, list(share_participant_ids))
    if sum(shares.values()) != amount_minor:  # pragma: no cover - 도메인 불변식 (NFR-014)
        raise RuntimeError("분담액 합계가 원금과 다르다")

    with transaction(conn):
        repo_expenses.insert_expense(
            conn,
            expense_id=expense_id,
            trip_id=trip_id,
            payer_id=payer_id,
            amount_minor=amount_minor,
            currency=CURRENCY,
            note=note,
            spot_id=spot_id,
            spent_at=when,
            created_at=now,
        )
        repo_expenses.replace_shares(conn, expense_id, shares)
        repo_trips.bump_revision(conn, trip_id)

    row = repo_expenses.get_expense(conn, trip_id, expense_id)
    if row is None:  # pragma: no cover
        raise RuntimeError("경비 생성 직후 조회에 실패했다")
    return expense_payload(row, shares, rate_micro)


def update_expense(
    conn: sqlite3.Connection,
    *,
    trip: sqlite3.Row,
    expense_id: str,
    version: int,
    changes: Mapping[str, Any],
    share_participant_ids: Sequence[str] | None,
    rate_micro: int | None,
) -> dict[str, Any]:
    """낙관적 잠금 수정 (NFR-012). 금액·분담자가 바뀌면 분담액을 다시 확정 저장한다."""
    trip_id = str(trip["id"])
    row = repo_expenses.get_expense(conn, trip_id, expense_id)
    if row is None:
        raise NotFoundError("경비를 찾을 수 없습니다.")

    members = {str(item["id"]) for item in repo_trips.list_participants(conn, trip_id)}
    if "payer_id" in changes:
        _require_member(str(changes["payer_id"]), members, "payer_id")
    if share_participant_ids is not None:
        _require_share_ids(share_participant_ids, members)
    if changes.get("spot_id") is not None and repo_spots.get_spot(conn, trip_id, str(changes["spot_id"])) is None:
        raise UnprocessableError("해당 여행의 스팟이 아닙니다.", code="unknown_spot")

    amount_minor = int(changes.get("amount_minor", row["amount_minor"]))
    existing_shares = repo_expenses.list_shares(conn, trip_id).get(expense_id, {})
    share_ids = list(share_participant_ids) if share_participant_ids is not None else sorted(existing_shares)
    shares = split_even(amount_minor, share_ids)

    with transaction(conn):
        updated = repo_expenses.update_expense(
            conn,
            expense_id=expense_id,
            expected_version=version,
            fields=dict(changes),
        )
        if not updated:
            current = repo_expenses.get_expense(conn, trip_id, expense_id)
            raise VersionConflictError(expense_payload(current, existing_shares, rate_micro) if current else {})
        repo_expenses.replace_shares(conn, expense_id, shares)
        repo_trips.bump_revision(conn, trip_id)

    fresh = repo_expenses.get_expense(conn, trip_id, expense_id)
    if fresh is None:  # pragma: no cover
        raise RuntimeError("경비 수정 직후 조회에 실패했다")
    return expense_payload(fresh, shares, rate_micro)


def delete_expense(conn: sqlite3.Connection, *, trip: sqlite3.Row, expense_id: str) -> None:
    trip_id = str(trip["id"])
    if repo_expenses.get_expense(conn, trip_id, expense_id) is None:
        raise NotFoundError("경비를 찾을 수 없습니다.")
    with transaction(conn):
        repo_expenses.delete_expense(conn, expense_id)  # share 는 CASCADE 로 함께 사라진다
        repo_trips.bump_revision(conn, trip_id)


def build_settlement(conn: sqlite3.Connection, trip_id: str) -> dict[str, Any]:
    """최소 송금 정산 (REQ-008 · AC-014~AC-016).

    잔액 = 낸 돈 − 부담해야 할 돈이고, **합계는 항상 0** 이다(NFR-014). 서버가 그것을
    assert 한다 — 어긋나면 데이터가 깨진 것이지 표시 문제가 아니다.
    """
    participants = repo_trips.list_participants(conn, trip_id)
    paid_raw = repo_expenses.sum_paid_by_participant(conn, trip_id)
    owed_raw = repo_expenses.sum_owed_by_participant(conn, trip_id)
    ids = [str(row["id"]) for row in participants]
    paid = {participant_id: int(paid_raw.get(participant_id, 0)) for participant_id in ids}
    owed = {participant_id: int(owed_raw.get(participant_id, 0)) for participant_id in ids}

    balance = compute_balances(paid, owed)
    if sum(balance.values()) != 0:  # pragma: no cover - 불변식
        raise RuntimeError("잔액 합계가 0 이 아니다")

    names = {str(row["id"]): str(row["display_name"]) for row in participants}
    return {
        "currency": CURRENCY,
        "balances": [
            {
                "participant_id": participant_id,
                "display_name": names.get(participant_id, ""),
                "paid_minor": paid[participant_id],
                "owed_minor": owed[participant_id],
                "balance_minor": int(balance.get(participant_id, 0)),
            }
            for participant_id in sorted(ids)
        ],
        "transfers": [
            {
                "from_participant_id": transfer.from_participant_id,
                "to_participant_id": transfer.to_participant_id,
                "amount_minor": transfer.amount_minor,
            }
            for transfer in settle(balance)
        ],
    }


# ── 내부 ──────────────────────────────────────────────────────────────────
def _require_member(participant_id: str, members: set[str], field: str) -> None:
    if participant_id not in members:
        raise UnprocessableError(f"{field} 가 이 여행의 참가자가 아닙니다.", code="unknown_participant")


def _require_share_ids(share_participant_ids: Sequence[str], members: set[str]) -> None:
    if not share_participant_ids:
        raise UnprocessableError("분담 대상자가 비어 있습니다.", code="empty_shares")
    if len(set(share_participant_ids)) != len(share_participant_ids):
        raise UnprocessableError("분담 대상자가 중복됩니다.", code="duplicate_shares")
    for participant_id in share_participant_ids:
        _require_member(participant_id, members, "share_participant_ids")


def _normalize_instant(value: str) -> str:
    try:
        return format_iso_utc(parse_iso_utc(value))
    except ValueError as exc:
        raise UnprocessableError("spent_at 이 ISO-8601 시각이 아닙니다.", code="invalid_spent_at") from exc
