"""베이커(`tools/`)에 대한 테스트 — **고정 입력만 쓴다**.

`tools/` 는 pytest 수집 대상 밖이고(`pyproject.toml` 의 `testpaths`), 여기서 import 하는
것은 `bakery.io` 의 순수 직렬화 함수와 커밋된 입력 파일(JSON)뿐이다. HTTP 배선은
건드리지 않는다 — 베이커는 테스트에서 실행되지 않는다(NFR-019 · AC-081).
"""
