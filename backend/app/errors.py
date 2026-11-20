"""One error contract: {"error": {code, message, retryable, agent?, request_id}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger, redact, request_id_var

log = get_logger("errors")

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    503: "upstream_unavailable",
}


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        retryable: bool = False,
        agent: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.agent = agent
        self.headers = headers or {}

    def to_dict(self) -> dict[str, Any]:
        return error_body(self.code, self.message, self.retryable, self.agent)


def error_body(code: str, message: str, retryable: bool = False, agent: str | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {
        "code": code,
        "message": redact(message),
        "retryable": retryable,
        "request_id": request_id_var.get(),
    }
    if agent:
        err["agent"] = agent
    return {"error": err}


def not_found(what: str) -> AppError:
    return AppError("not_found", f"{what} not found", 404)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        msg = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(error_body(code, msg, exc.status_code >= 500), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = []
        for e in exc.errors()[:5]:
            loc = ".".join(str(p) for p in e.get("loc", []) if p != "body")
            parts.append(f"{loc}: {e.get('msg')}")
        return JSONResponse(
            error_body("validation_error", "; ".join(parts) or "invalid request"), status_code=422
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error=redact(repr(exc)))
        return JSONResponse(error_body("internal", "Internal error", retryable=True), status_code=500)
