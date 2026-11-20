"""Request-id propagation and access logging."""

from __future__ import annotations

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging import get_logger, request_id_var

log = get_logger("http")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _SAFE_ID.match(incoming) else f"req_{uuid.uuid4().hex[:16]}"
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        if request.url.path.startswith("/api/"):
            log.info(
                "http_request",
                request_id=rid,
                method=request.method,
                path=request.url.path,  # never log query strings
                status=response.status_code,
                ms=round((time.perf_counter() - start) * 1000, 1),
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response
