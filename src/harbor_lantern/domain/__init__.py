"""순수 계산 계층 (DSN-01 · DSN-02).

**이 패키지는 I/O 를 하지 않는다.** `storage/`·`api/`·`httpx`·`datetime.now()`·`zoneinfo` 를
import 하지 않으며, 필요한 시각은 전부 인자로 받는다. 이 규칙이 깨지면
`tests/static/test_layering.py` 가 실패한다(설계서 §2.2).
"""

from __future__ import annotations
