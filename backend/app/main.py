"""FastAPI application: API under /api/v1, built SPA served from the same origin."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import APP_VERSION, ROOT, SCHEMA_VERSION, get_settings
from app.db import Run, init_db, session_scope, utcnow
from app.errors import error_body, install_error_handlers
from app.logging import configure_logging, get_logger
from app.middleware import RequestIdMiddleware, SecurityHeadersMiddleware

log = get_logger("main")


def _recover_orphaned_runs() -> None:
    """Runs executing in a previous process can't continue after a restart: mark them failed (retryable)."""
    from sqlalchemy import select

    from app.engine import EventLog

    with session_scope() as s:
        stuck = s.scalars(select(Run).where(Run.status.in_(["queued", "running"]))).all()
        for r in stuck:
            r.status, r.finished_at = "failed", utcnow()
            r.error = {
                "code": "interrupted",
                "message": "The server restarted during this run. Please re-run.",
                "retryable": True,
            }
            EventLog(r.id).emit("run.failed", None, error=r.error)
        if stuck:
            log.warning("recovered_orphaned_runs", count=len(stuck))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.LOG_LEVEL, s.LOG_JSON or s.is_production)
    if s.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.init(
            dsn=s.SENTRY_DSN,
            environment=s.ENV,
            release=s.GIT_SHA,
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
    init_db()
    _recover_orphaned_runs()
    log.info(
        "startup",
        version=APP_VERSION,
        fixture_mode=s.fixture_mode,
        durable=s.durable_storage,
        auth=s.auth_configured,
        env=s.ENV,
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Earnings Intelligence Platform API",
        version=f"{APP_VERSION} (schema {SCHEMA_VERSION})",
        description="Grounded earnings research. Every number carries a source and an as-of time. Research only, not advice.",
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(router)

    original_openapi = app.openapi

    def openapi() -> dict:  # type: ignore[type-arg]
        spec = original_openapi()
        spec["info"]["x-schema-version"] = SCHEMA_VERSION
        return spec

    app.openapi = openapi  # type: ignore[method-assign]

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    async def spa(path: str, request: Request) -> FileResponse | HTMLResponse | JSONResponse:
        if path.startswith("api/"):
            return JSONResponse(error_body("not_found", "Unknown API route"), status_code=404)
        f = dist / path
        if path and f.is_file() and dist in f.resolve().parents:
            return FileResponse(f)
        index = dist / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(
            "<h1>Earnings Intelligence API</h1><p>UI not built. See <a href='/api/v1/docs'>API docs</a>.</p>"
        )

    return app


app = create_app()
