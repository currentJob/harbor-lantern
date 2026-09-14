"""HTTP 라우트 묶음 (설계서 §9).

명시적 패키지다. `__init__.py` 가 없으면 namespace package 로 **우연히** import 되는데,
그때는 휠에 포함되는지·어떤 경로가 먼저 잡히는지가 설치 형태에 따라 달라진다.
"""

from __future__ import annotations

__all__ = ["expenses", "external", "meta", "spots", "trips"]
