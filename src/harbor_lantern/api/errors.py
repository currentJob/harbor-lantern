"""통일 에러 본문과 예외 핸들러 (설계서 §7.2 · contracts/openapi.yaml `Error`).

본문은 **한 모양만** 쓴다: `{"error": "<코드>", "message": "<한국어 설명>", "detail": {...}?}`.
프론트가 분기할 것은 상태코드와 `error` 코드 둘뿐이고, 사람이 읽는 것은 `message` 다.

서비스 계층도 이 예외들을 던진다. 설계서 §9 의 파일 트리에 `services/errors.py` 가
없어서 여기 한 곳에 모았다 — 두 곳에 나눠 두면 같은 뜻의 예외가 두 벌 생긴다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = [
    "ApiError",
    "ConflictError",
    "NotFoundError",
    "RateLimitedError",
    "RevisionConflictError",
    "UnprocessableError",
    "VersionConflictError",
    "error_body",
    "install_error_handlers",
]


def error_body(code: str, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return body


class ApiError(Exception):
    """HTTP 상태코드를 들고 다니는 예외. 핸들러가 통일 본문으로 바꾼다."""

    status_code = 500
    code = "internal_error"
    message = "서버 오류가 발생했습니다."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        detail: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message if message is not None else type(self).message
        self.code = code if code is not None else type(self).code
        self.detail = detail
        self.headers = headers
        self.status_code = status_code if status_code is not None else type(self).status_code
        super().__init__(self.message)

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=jsonable_encoder(error_body(self.code, self.message, self.detail)),
            headers=self.headers,
        )


class NotFoundError(ApiError):
    """대상이 없거나 **접근 권한이 없다 — 구분하지 않는다.**

    토큰의 여행과 경로의 `trip_id` 가 달라도 403 이 아니라 404 다. 403 은
    "그 자원은 있다"를 알려 주는 신호이고, 무계정 공유 모델에서 그건 열거 창구가 된다.
    """

    status_code = 404
    code = "not_found"
    message = "대상을 찾을 수 없습니다."


class UnprocessableError(ApiError):
    status_code = 422
    code = "unprocessable"
    message = "입력값이 올바르지 않습니다."


class ConflictError(ApiError):
    status_code = 409
    code = "conflict"
    message = "요청이 현재 상태와 충돌합니다."


class VersionConflictError(ApiError):
    """리소스 낙관적 잠금 실패 — `detail.current` 에 최신 리소스를 싣는다(§6.18)."""

    status_code = 409
    code = "version_conflict"
    message = "다른 사람이 먼저 수정했습니다. 최신 내용을 확인하세요."

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__(detail={"current": current})


class RevisionConflictError(ApiError):
    """여행 동기화 리비전 불일치 — 재정렬·이동이 쓰는 쪽이다(§6.11 · §12 F3)."""

    status_code = 409
    code = "revision_conflict"
    message = "다른 사람이 먼저 순서를 바꿨습니다. 최신 상태를 받아 다시 시도하세요."

    def __init__(self, current_revision: int) -> None:
        super().__init__(detail={"current_revision": current_revision})


class RateLimitedError(ApiError):
    status_code = 429
    code = "rate_limited"
    message = "참여 시도가 너무 많습니다. 잠시 후 다시 시도하세요."

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(
            detail={"retry_after_s": retry_after_s},
            headers={"Retry-After": str(retry_after_s)},
        )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic 의 원본 오류 목록을 `detail.errors` 에 그대로 싣는다. 프론트는
        # 코드로 분기하고, 개발자는 목록으로 어디가 틀렸는지 본다.
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                error_body(
                    "validation_error",
                    "입력값이 올바르지 않습니다.",
                    {"errors": exc.errors()},
                )
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
        message = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(error_body(code, message)),
            headers=getattr(exc, "headers", None),
        )
