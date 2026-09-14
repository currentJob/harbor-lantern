# 배포 전 정찰

2026-09-13. 모드: LEGACY_INTEGRATION. 대상: 현재 Harbor Lantern 프로젝트.

기존 FastAPI/Pydantic 계약, SQLite 저장소, 도메인·서비스, 바닐라 JS 화면이 있다.
서버 팩토리와 일정·경비·외부 API 라우터가 없어 화면에서 실제 서비스를 호출할 수 없었다.
요구사항·설계서만 존재하며 표준 문서 세트는 미완성이다. Git 원격과 최초 커밋이 없었다.

기존 계층·한국어 오류·참가자 토큰 방식과 고정 의존성을 보존한다.
수정 허용 범위: API 연결, 실행·정적 내보내기 도구, 프런트 API 주소 설정,
연결 테스트에서 확인한 서비스 오류, 관련 검증·운영 문서와 의존성 호환 설정.
원래 여행 시드와 화면 디자인은 유지한다.

회귀 명령: `.venv/Scripts/python.exe -m pytest -q`, `.venv/Scripts/python.exe -m ruff check .`.
Windows sandbox에서 기존 pytest 캐시 접근이 거부되어 검증은 별도 임시 디렉터리와 캐시 비활성화로 실행한다.
