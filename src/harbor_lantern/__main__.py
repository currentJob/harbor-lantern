"""원커맨드 실행 — `uv run python -m harbor_lantern` (설계서 §9 · DSN-24).

`uvicorn` 을 **팩토리 모드**로 부른다. `create_app` 이 인자 없이도 동작해야 하는 이유가
이것이다 — 설정은 `load_settings()` 가 환경변수에서 읽는다(`HL_*`).

host/port 는 `Settings` 에서 온다(`HL_HOST`·`HL_PORT`). 기본은 `127.0.0.1:8080` 이고,
**의도적으로 루프백이다** — 초대코드를 아는 사람이 전체 편집 권한을 갖는 모델(A4)이라
기본값이 `0.0.0.0` 이면 같은 네트워크의 아무나 붙는다.
"""

from __future__ import annotations

import sys

import uvicorn

from harbor_lantern.config import load_settings

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    del argv  # 옵션은 환경변수로만 받는다(설정 단일 소스 — DSN-04)
    settings = load_settings()
    uvicorn.run(
        "harbor_lantern.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
