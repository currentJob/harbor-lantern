"""헬스체크와 프론트 진입점 (설계서 §7.2 · §9 — `api/routes/meta.py`).

**경로가 `/api/health` 인 이유.** 설계서 §7.2 는 `/healthz` 라고 적었지만, 구현·프론트·
정적 내보내기(`web/config.js`·`tools/export_pages.py`)가 이미 `/api/health` 를 쓰고 있다.
경로 하나를 지금 바꾸면 프론트와 계약을 함께 고쳐야 하고, 얻는 것은 이름뿐이다 —
그래서 **구현을 SSoT 로 삼아 `contracts/openapi.yaml` 을 `/api/health` 로 맞췄다**
(이 불일치는 개발내역서에 남긴다). 덤으로 `/api/*` 는 CORS·`Cache-Control: no-store`
규칙을 한 번에 받는다 — `/healthz` 는 그 밖이라 예외를 하나 더 만들었을 자리다.

`GET /` 는 `StaticFiles` 마운트에 맡기지 않고 **명시적 라우트**로 둔다. 마운트는
OpenAPI 스키마에 나타나지 않아서, 계약에 적힌 `/` 가 구현과 대조되지 않기 때문이다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

from harbor_lantern import __version__
from harbor_lantern.api.schemas import HealthOut

__all__ = ["WEB_DIR", "router"]

router = APIRouter(tags=["meta"])

# routes/meta.py → api → harbor_lantern
WEB_DIR = Path(__file__).resolve().parents[2] / "web"

_PLACEHOLDER = (
    "<!doctype html><meta charset='utf-8'><title>Harbor Lantern</title>"
    "<p>프론트 자산이 아직 설치되지 않았습니다.</p>"
)


@router.get("/api/health", response_model=HealthOut, summary="헬스체크")
def health() -> dict[str, str]:
    """살아 있는가. 기동 확인·터널 점검이 이 한 줄을 본다."""
    return {"status": "ok", "version": __version__}


@router.get(
    "/",
    response_class=HTMLResponse,
    response_model=None,  # 반환 타입이 Response 두 종류라 스키마를 유도하지 않는다
    summary="프론트엔드 (정적 서빙 — 외부 CDN 참조 0건)",
)
def index() -> HTMLResponse | FileResponse:
    """`web/index.html`. 나머지 자산은 `StaticFiles` 마운트가 이어받는다(NFR-015)."""
    document = WEB_DIR / "index.html"
    if not document.is_file():  # pragma: no cover - 프론트 자산이 빠진 설치
        return HTMLResponse(_PLACEHOLDER)
    return FileResponse(document, media_type="text/html")
