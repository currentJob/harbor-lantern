"""환율 어댑터 — Frankfurter HKD→KRW (설계서 §6.13 · REQ-015).

API 키가 필요 없다. TTL 21600초(6시간)의 근거는 ECB 가 영업일 16:00 CET 에 1회만
갱신한다는 것이고, 실측이 이를 확인해 준다 — 2026-09-13(토) 조회에 `date: 2026-09-11`
(금)이 돌아왔다.

**`date` 는 조회일이 아니다.** 주말·공휴일에는 직전 영업일 값이 온다. 이것은 고장이
아니므로 `stale` 로 표시하지 않고 `rate_date` 로 그대로 노출한다 — 둘을 섞으면
사용자가 매 주말 고장 신고를 한다.
"""

from __future__ import annotations

from typing import Any

import httpx

from harbor_lantern.config import ExternalConfig
from harbor_lantern.domain.util import round_half_up
from harbor_lantern.services.external.ports import ExternalUnavailable, FxSnapshot

__all__ = ["BASE", "QUOTE", "FrankfurterFxAdapter"]

BASE = "HKD"
QUOTE = "KRW"


class FrankfurterFxAdapter:
    """`FxPort` 구현."""

    def __init__(self, cfg: ExternalConfig, client: httpx.Client | None = None) -> None:
        self._cfg = cfg
        self._client = client

    def fetch(self) -> FxSnapshot:
        params = {"base": BASE, "symbols": QUOTE}
        try:
            if self._client is not None:
                response = self._client.get(self._cfg.fx_url, params=params)
            else:
                with httpx.Client(timeout=self._cfg.timeout_s) as client:
                    response = client.get(self._cfg.fx_url, params=params)
            response.raise_for_status()
            document = response.json()
        except Exception as exc:
            raise ExternalUnavailable(f"frankfurter 호출 실패: {exc}") from exc
        return _to_snapshot(document)


def _to_snapshot(document: Any) -> FxSnapshot:
    if not isinstance(document, dict):
        raise ExternalUnavailable("frankfurter 응답이 객체가 아니다")
    rates = document.get("rates")
    if not isinstance(rates, dict) or QUOTE not in rates:
        raise ExternalUnavailable(f"frankfurter 응답에 {QUOTE} 환율이 없다")
    rate = rates[QUOTE]
    if isinstance(rate, bool) or not isinstance(rate, int | float) or rate <= 0:
        raise ExternalUnavailable(f"frankfurter 환율이 숫자가 아니다: {rate!r}")
    rate_date = document.get("date")
    return FxSnapshot(
        base=BASE,
        quote=QUOTE,
        # 환율도 정수로 굳혀서 저장한다(NFR-014). `round()` 는 은행가 반올림이라 쓰지
        # 않는다 — 경계값에서만 갈리는 종류의 오차다(§12 F4).
        rate_micro=round_half_up(float(rate) * 1_000_000),
        rate_date=rate_date if isinstance(rate_date, str) and rate_date else None,
    )
