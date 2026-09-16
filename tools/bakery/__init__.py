"""빌드 타임 베이커의 배선 모듈들 (DSN-47 · 설계서 §16.18).

여기 있는 것은 **HTTP 배선과 파일 쓰기뿐**이다. 판단 — 분류·중복 접기·연결 검증·
선별·등급 — 은 전부 `harbor_lantern.domain` 의 순수 함수가 한다. 그 분할이
NFR-019(베이커를 테스트에서 실행하지 않는다)와 "그래도 베이커의 판단을 검증해야
한다"를 동시에 만족시키는 유일한 방법이다(설계서 §16.2).

`tools/` 는 pytest 수집 대상 밖이다(`pyproject.toml` 의 `testpaths = ["tests"]`).
`tests/tools/` 가 import 하는 것은 `bakery.io` 의 순수 직렬화 함수 하나뿐이고,
HTTP 를 들고 있는 나머지 모듈은 테스트에서 건드리지 않는다(AC-081).
"""

from __future__ import annotations

__all__: list[str] = []
