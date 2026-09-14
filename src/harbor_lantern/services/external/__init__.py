"""외부 공급자 어댑터 (DSN-20).

**아웃바운드 HTTP 는 이 패키지 안에서만 일어난다.** 다른 곳에 `httpx` 가 나타나면
`tests/static/test_layering.py` 가 실패한다(설계서 §2.2).
"""

from __future__ import annotations
