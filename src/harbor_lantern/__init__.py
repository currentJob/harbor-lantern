"""Harbor Lantern — 홍콩 3박4일 동적 여행 가이드.

정적 HTML 한 장(`reference/original-static-page.html`)을 서버 기반 공유 가이드로 옮긴 것이다.
배포 단위는 파이썬 프로세스 하나이고, FastAPI 가 API 와 정적 프론트를 함께 서빙한다.

설계서: `docs/02_설계서.md` (DOC-HKG-02) — DSN-01.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
