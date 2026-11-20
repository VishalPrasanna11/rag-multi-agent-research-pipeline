"""HTTP API (/api/v1). Every user-data route is scoped by the caller's identity (JWT sub or anon cookie)."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import desc, select
from sse_starlette.sse import EventSourceResponse

from app import engine
from app.auth import Identity, check_rate, current_identity
from app.config import APP_VERSION, SCHEMA_VERSION, get_settings
from app.db import (
    AuditLog,
    Feedback,
    Notification,
    Prediction,
    ReportRow,
    Review,
    Run,
    ShareToken,
    User,
    WatchlistItem,
    audit,
    get_engine,
    session_scope,
    utcnow,
)
from app.errors import AppError, not_found
from app.facts import strip_tokens
from app.logging import get_logger
from app.schemas import (
    DISCLAIMER,
    FeedbackIn,
    PreferencesIn,
    Report,
    ReviewDecision,
    RunCreate,
    RunMode,
    RunResume,
    RunSummary,
    StreamEvent,
    WatchlistItemIn,
)

log = get_logger("api")
router = APIRouter(prefix="/api/v1")
_tasks: set[asyncio.Task[Any]] = set()


# ---------------------------------------------------------------- meta


@router.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    s = get_settings()
    checks: dict[str, Any] = {}
    try:
        with get_engine().connect() as c:
            c.exec_driver_sql("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    if s.REDIS_URL:
        try:
            import redis

            redis.from_url(
                s.REDIS_URL, ssl_cert_reqs=None, socket_timeout=2
            ).ping()  # Heroku KV: self-signed TLS
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {
        "status": status,
        "version": APP_VERSION,
        "git_sha": s.GIT_SHA[:12],
        "schema_version": SCHEMA_VERSION,
        "env": s.ENV,
        "fixture_mode": s.fixture_mode,
        "durable_storage": s.durable_storage,
        "checks": checks,
    }


@router.get("/meta", tags=["meta"])
def meta() -> dict[str, Any]:
    s = get_settings()
    return {
        "version": APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "fixture_mode": s.fixture_mode,
        "durable_storage": s.durable_storage,
        "auth": {
            "configured": s.auth_configured,
            "provider": "clerk" if s.CLERK_PUBLISHABLE_KEY else "oidc",
            "clerk_publishable_key": s.CLERK_PUBLISHABLE_KEY or None,
            "allow_anonymous": s.ALLOW_ANONYMOUS,
        },
        "needs": s.missing_keys(),
        "disclaimer": DISCLAIMER,
        "data_sources": [
            {
                "name": "SEC EDGAR",
                "use": "filings, XBRL financials, 8-K items, Form 4",
                "license": "Public domain",
            },
            {
                "name": "Financial Modeling Prep",
                "use": "consensus and actual EPS/revenue, calendar",
                "license": "Free tier, display only",
            },
            {
                "name": "Yahoo Finance (yfinance)",
                "use": "prices, options, short interest",
                "license": "Unofficial library; Yahoo terms allow personal use only",
            },
            {"name": "Tavily", "use": "dated news headlines", "license": "Links and headlines only"},
        ],
    }


# ---------------------------------------------------------------- runs


def _summary(r: Run, review_status: str | None = None) -> RunSummary:
    return RunSummary(
        id=r.id,
        ticker=r.ticker,
        mode=RunMode(r.mode),
        status=r.status,
        query=r.query,
        created_at=r.created_at,  # type: ignore[arg-type]
        finished_at=r.finished_at,
        report_id=r.report_id,
        fixture=r.fixture,
        cost_usd=round(r.cost_usd, 4),
        review_status=review_status,
    )


def _spawn(fn: Any, *args: Any) -> None:
    task = asyncio.get_running_loop().create_task(asyncio.to_thread(fn, *args))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.post("/runs", status_code=202, tags=["runs"])
async def create_run(body: RunCreate, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    s = get_settings()
    fixture = s.fixture_mode or bool(body.fixture)
    if body.fixture is False and s.fixture_mode:
        raise AppError("upstream_unavailable", "Live research is unavailable: no LLM key is configured", 503)
    check_rate(ident, "runs", s.ANON_RUNS_PER_HOUR if ident.anonymous and not fixture else s.RUNS_PER_HOUR)
    inputs: dict[str, Any] = {}
    if body.transcript_text:
        inputs.update(transcript_text=body.transcript_text, transcript_label=body.transcript_label)
    if s.EVAL_HOOKS and not s.is_production:
        if body.debug_plant:
            inputs["debug_plant"] = body.debug_plant
        if body.debug_fail:
            inputs["debug_fail"] = body.debug_fail
    run = engine.create_run(
        ident.sub, body.ticker, body.mode, body.query, body.as_of, body.event_date, fixture, inputs or None
    )
    _spawn(engine.execute, run.id)
    return {
        "run_id": run.id,
        "status": "queued",
        "fixture": fixture,
        "events_url": f"/api/v1/runs/{run.id}/events",
    }


def _own_run(sess: Any, run_id: str, ident: Identity) -> Run:
    r = sess.get(Run, run_id)
    if r is None or r.user_id != ident.sub:
        raise not_found("Run")
    return r


@router.get("/runs", tags=["runs"])
def list_runs(
    ident: Identity = Depends(current_identity), ticker: str | None = None, limit: int = Query(30, le=100)
) -> list[RunSummary]:
    with session_scope() as s:
        q = select(Run).where(Run.user_id == ident.sub).order_by(desc(Run.created_at)).limit(limit)
        if ticker:
            q = q.where(Run.ticker == ticker.upper())
        runs = list(s.scalars(q).all())
        reviews = {
            r.id: r.review_status
            for r in s.scalars(
                select(ReportRow).where(ReportRow.id.in_([x.report_id for x in runs if x.report_id]))
            ).all()
        }
        return [_summary(r, reviews.get(r.report_id or "")) for r in runs]


@router.get("/runs/{run_id}", tags=["runs"])
def get_run(run_id: str, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        r = _own_run(s, run_id, ident)
        return {
            **_summary(r).model_dump(mode="json"),
            "pending": r.pending,
            "error": r.error,
            "as_of": r.as_of.isoformat(),
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
        }


@router.get("/runs/{run_id}/events", tags=["runs"])
async def run_events(
    run_id: str,
    request: Request,
    ident: Identity = Depends(current_identity),
    last_event_id: int | None = Query(None, alias="after"),
) -> EventSourceResponse:
    with session_scope() as s:
        _own_run(s, run_id, ident)
    hdr = request.headers.get("last-event-id")
    after = int(hdr) if hdr and hdr.isdigit() else (last_event_id or 0)

    async def gen() -> AsyncIterator[dict[str, Any]]:
        nonlocal after
        idle = 0.0
        while True:
            if await request.is_disconnected():
                return
            evs = await asyncio.to_thread(engine.events_after, run_id, after)
            for e in evs:
                after = e.seq
                payload = StreamEvent(
                    run_id=run_id,
                    seq=e.seq,
                    ts=e.ts,
                    type=e.type,  # type: ignore[arg-type]  # values come from our own emitter
                    agent=e.agent,
                    payload=e.payload,
                )
                yield {"id": str(e.seq), "event": e.type, "data": payload.model_dump_json()}
                if e.type in engine.TERMINAL or e.type == "clarification.required":
                    return  # clarification: the client answers, then reconnects with Last-Event-ID
            if not evs:
                status = await asyncio.to_thread(_run_status, run_id)
                if status in ("awaiting_input",) and idle > 1800:
                    return
                if status in ("failed", "cancelled", "completed", "awaiting_review") and idle > 2:
                    return
            idle = 0.0 if evs else idle + 0.4
            await asyncio.sleep(0.4)

    return EventSourceResponse(
        gen(), ping=15, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


def _run_status(run_id: str) -> str | None:
    with session_scope() as s:
        r = s.get(Run, run_id)
        return r.status if r else None


@router.post("/runs/{run_id}/cancel", tags=["runs"])
def cancel_run(run_id: str, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        r = _own_run(s, run_id, ident)
        if r.status in ("completed", "failed", "cancelled"):
            raise AppError("conflict", f"Run is already {r.status}", 409)
        r.cancel_requested = True
        if r.status in ("queued", "awaiting_input"):
            r.status, r.finished_at = "cancelled", utcnow()
            engine.EventLog(run_id).emit("run.cancelled", None, message="Stopped by user")
        audit(s, ident.sub, "run.cancel", "run", run_id)
    return {"run_id": run_id, "cancel_requested": True}


@router.post("/runs/{run_id}/resume", tags=["runs"])
async def resume_run(
    run_id: str, body: RunResume, ident: Identity = Depends(current_identity)
) -> dict[str, Any]:
    with session_scope() as s:
        r = _own_run(s, run_id, ident)
        if r.status != "awaiting_input":
            raise AppError("conflict", "Run is not waiting for an answer", 409)
        if not body.answer:
            raise AppError("validation_error", "answer is required", 422)
        r.status = "running"
        audit(s, ident.sub, "run.clarify", "run", run_id, answer=body.answer)
    if not engine.has_checkpoint(run_id):
        raise AppError(
            "conflict", "This run can no longer be resumed (server restarted). Start a new run.", 409
        )
    _spawn(engine.execute, run_id, body.answer.strip().upper())
    return {"run_id": run_id, "status": "running"}


# ---------------------------------------------------------------- reports


def _own_report(sess: Any, report_id: str, ident: Identity) -> ReportRow:
    row = sess.get(ReportRow, report_id)
    if row is None or row.user_id != ident.sub:
        raise not_found("Report")
    return row


@router.get("/reports", tags=["reports"])
def list_reports(
    ident: Identity = Depends(current_identity),
    ticker: str | None = None,
    saved: bool | None = None,
    limit: int = Query(30, le=100),
) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = (
            select(ReportRow)
            .where(ReportRow.user_id == ident.sub)
            .order_by(desc(ReportRow.created_at))
            .limit(limit)
        )
        if ticker:
            q = q.where(ReportRow.ticker == ticker.upper())
        if saved is not None:
            q = q.where(ReportRow.saved == saved)
        out = []
        for r in s.scalars(q).all():
            b = r.body
            brief = b.get("brief") or {}
            out.append(
                {
                    "id": r.id,
                    "run_id": r.run_id,
                    "ticker": r.ticker,
                    "mode": r.mode,
                    "saved": r.saved,
                    "review_status": r.review_status,
                    "created_at": r.created_at.isoformat(),
                    "headline": strip_tokens(brief.get("headline", "")),
                    "fixture": b.get("fixture", False),
                    "event": b.get("event"),
                    "confidence": (brief.get("confidence") or {}).get("level"),
                }
            )
        return out


@router.get("/reports/{report_id}", tags=["reports"])
def get_report(report_id: str, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        row = _own_report(s, report_id, ident)
        reviews = s.scalars(
            select(Review).where(Review.report_id == report_id).order_by(Review.created_at)
        ).all()
        return {
            **row.body,
            "saved": row.saved,
            "review_status": row.review_status,
            "reviews": [
                {
                    "decision": v.decision,
                    "notes": v.notes,
                    "reviewer": v.reviewer_id,
                    "created_at": v.created_at.isoformat(),
                }
                for v in reviews
            ],
        }


@router.post("/reports/{report_id}/review", tags=["reports"])
async def review_report(
    report_id: str, body: RunResume, ident: Identity = Depends(current_identity)
) -> dict[str, Any]:
    if body.decision is None:
        raise AppError("validation_error", "decision is required", 422)
    with session_scope() as s:
        row = _own_report(s, report_id, ident)
        run_id = row.run_id
        if body.decision == ReviewDecision.REJECTED and not (body.notes or "").strip():
            raise AppError("validation_error", "A note is required when rejecting a report", 422)
    payload = {"decision": body.decision, "notes": body.notes, "actor": ident.sub}
    if engine.has_checkpoint(run_id):
        await asyncio.to_thread(engine.execute, run_id, payload)
    else:
        engine.apply_review(run_id, report_id, str(body.decision), body.notes, ident.sub)
    return {"report_id": report_id, "review_status": body.decision}


@router.get("/reports/{report_id}/audit", tags=["reports"])
def report_audit(report_id: str, ident: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    with session_scope() as s:
        row = _own_report(s, report_id, ident)
        rows = s.scalars(
            select(AuditLog)
            .where(AuditLog.subject_id.in_([report_id, row.run_id]))
            .order_by(AuditLog.created_at)
        ).all()
        return [
            {
                "action": a.action,
                "actor": a.actor_id,
                "detail": a.detail,
                "request_id": a.request_id,
                "at": a.created_at.isoformat(),
            }
            for a in rows
        ]


@router.post("/reports/{report_id}/save", tags=["reports"])
def save_report(
    report_id: str, saved: bool = True, ident: Identity = Depends(current_identity)
) -> dict[str, Any]:
    with session_scope() as s:
        row = _own_report(s, report_id, ident)
        row.saved = saved
        audit(s, ident.sub, "report.save" if saved else "report.unsave", "report", report_id)
    return {"report_id": report_id, "saved": saved}


@router.post("/reports/{report_id}/feedback", status_code=201, tags=["reports"])
def report_feedback(
    report_id: str, body: FeedbackIn, ident: Identity = Depends(current_identity)
) -> dict[str, Any]:
    with session_scope() as s:
        _own_report(s, report_id, ident)
        s.add(
            Feedback(
                report_id=report_id,
                user_id=ident.sub,
                rating=body.rating,
                reasons=body.reasons,
                comment=body.comment,
                section_id=body.section_id,
            )
        )
    return {"ok": True}


@router.post("/reports/{report_id}/share", tags=["reports"])
def share_report(
    report_id: str, ident: Identity = Depends(current_identity), days: int = Query(30, ge=1, le=365)
) -> dict[str, Any]:
    with session_scope() as s:
        _own_report(s, report_id, ident)
        tok = secrets.token_urlsafe(24)
        s.add(
            ShareToken(
                token=tok,
                report_id=report_id,
                created_by=ident.sub,
                expires_at=utcnow() + timedelta(days=days),
            )
        )
        audit(s, ident.sub, "report.share", "report", report_id, days=days)
    return {"token": tok, "url": f"/share/{tok}", "expires_in_days": days}


@router.delete("/reports/{report_id}/share/{token}", tags=["reports"])
def revoke_share(report_id: str, token: str, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        _own_report(s, report_id, ident)
        st = s.get(ShareToken, token)
        if st is None or st.report_id != report_id:
            raise not_found("Share link")
        st.revoked = True
        audit(s, ident.sub, "report.share_revoke", "report", report_id)
    return {"revoked": True}


@router.get("/share/{token}", tags=["public"])
def shared_report(token: str) -> dict[str, Any]:
    with session_scope() as s:
        st = s.get(ShareToken, token)
        if st is None or st.revoked or (st.expires_at and st.expires_at < datetime.now(UTC)):
            raise not_found("Shared report")
        row = s.get(ReportRow, st.report_id)
        if row is None:
            raise not_found("Shared report")
        body = dict(row.body)
        body.pop("query", None)
        return {**body, "shared": True, "review_status": row.review_status}


@router.get("/reports/{report_id}/export.md", tags=["reports"], response_class=PlainTextResponse)
def export_markdown(report_id: str, ident: Identity = Depends(current_identity)) -> PlainTextResponse:
    with session_scope() as s:
        row = _own_report(s, report_id, ident)
        rep = Report.model_validate(row.body)
    return PlainTextResponse(
        report_markdown(rep),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{rep.ticker}-{rep.id}.md"'},
    )


def report_markdown(rep: Report) -> str:
    from app.market import NY

    out = [
        f"# {rep.ticker} — earnings research",
        "",
        f"As of {rep.as_of.astimezone(NY).date()} · event {rep.event.date} ({rep.event.date_status}, {rep.event.time_of_day}) · "
        f"review: {rep.review_status}" + (" · FIXTURE DATA" if rep.fixture else ""),
        "",
        f"> {rep.disclaimer}",
        "",
    ]
    if rep.brief:
        b = rep.brief
        out += [
            f"## {strip_tokens(b.headline)}",
            "",
            "**Setup**",
            *[f"- {strip_tokens(x)}" for x in b.setup],
            "",
            "| Scenario | Probability | Method | Range | What has to be true |",
            "|---|---|---|---|---|",
        ]
        for sc in b.scenarios:
            rng = f"{sc.move_range_pct[0]}% to {sc.move_range_pct[1]}%" if sc.move_range_pct else "—"
            out.append(
                f"| {sc.name} | {sc.probability:.0%} | {sc.probability_method} (n={sc.base_rate_n}) | {rng} | "
                f"{strip_tokens(sc.narrative)} |"
            )
        out += [
            "",
            f"**Confidence:** {b.confidence.explanation}",
            "",
            "**What would change the view**",
            *[f"- {strip_tokens(x)}" for x in b.what_would_change_view],
            "",
            "**Questions for the call**",
            *[f"- {strip_tokens(x)}" for x in b.questions_for_call],
            "",
            "**Key risks**",
            *[f"- {strip_tokens(x)}" for x in b.key_risks],
            "",
            "**Unknowns**",
            *[f"- {x}" for x in b.unknowns],
            "",
        ]
    for a in rep.agents:
        for sec in a.sections:
            out += [f"## {sec.title}", "", strip_tokens(sec.markdown), ""]
    out += ["## Sources", ""]
    for c in rep.citations.values():
        out.append(
            f"- [{c.id}] {c.title} — {c.source} — as of {c.as_of.astimezone(NY).date()}"
            + (f" — {c.url}" if c.url else "")
        )
    return "\n".join(out)


# ---------------------------------------------------------------- watchlist, notifications, preferences


@router.get("/watchlist", tags=["watchlist"])
def get_watchlist(ident: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    from app.services import next_event_for

    with session_scope() as s:
        items = s.scalars(
            select(WatchlistItem).where(WatchlistItem.user_id == ident.sub).order_by(WatchlistItem.ticker)
        ).all()
        rows = [(i.ticker, i.auto_run, i.added_at) for i in items]
    return [
        {"ticker": t, "auto_run": a, "added_at": d.isoformat(), "next_event": next_event_for(t)}
        for t, a, d in rows
    ]


@router.post("/watchlist", status_code=201, tags=["watchlist"])
def add_watchlist(body: WatchlistItemIn, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        if s.scalar(
            select(WatchlistItem).where(
                WatchlistItem.user_id == ident.sub, WatchlistItem.ticker == body.ticker
            )
        ):
            raise AppError("conflict", f"{body.ticker} is already on your watchlist", 409)
        n = len(s.scalars(select(WatchlistItem).where(WatchlistItem.user_id == ident.sub)).all())
        if n >= 50:
            raise AppError("conflict", "Watchlist is limited to 50 tickers", 409)
        s.add(WatchlistItem(user_id=ident.sub, ticker=body.ticker))
        audit(s, ident.sub, "watchlist.add", "ticker", body.ticker)
    return {"ticker": body.ticker}


@router.patch("/watchlist/{ticker}", tags=["watchlist"])
def update_watchlist(
    ticker: str, auto_run: bool, ident: Identity = Depends(current_identity)
) -> dict[str, Any]:
    with session_scope() as s:
        it = s.scalar(
            select(WatchlistItem).where(
                WatchlistItem.user_id == ident.sub, WatchlistItem.ticker == ticker.upper()
            )
        )
        if it is None:
            raise not_found("Watchlist item")
        it.auto_run = auto_run
    return {"ticker": ticker.upper(), "auto_run": auto_run}


@router.delete("/watchlist/{ticker}", tags=["watchlist"])
def remove_watchlist(ticker: str, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        it = s.scalar(
            select(WatchlistItem).where(
                WatchlistItem.user_id == ident.sub, WatchlistItem.ticker == ticker.upper()
            )
        )
        if it is None:
            raise not_found("Watchlist item")
        s.delete(it)
        audit(s, ident.sub, "watchlist.remove", "ticker", ticker.upper())
    return {"removed": ticker.upper()}


@router.get("/notifications", tags=["notifications"])
def notifications(ident: Identity = Depends(current_identity), unread: bool = False) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = (
            select(Notification)
            .where(Notification.user_id == ident.sub)
            .order_by(desc(Notification.created_at))
            .limit(50)
        )
        if unread:
            q = q.where(Notification.read.is_(False))
        return [
            {
                "id": n.id,
                "kind": n.kind,
                "ticker": n.ticker,
                "title": n.title,
                "body": n.body,
                "read": n.read,
                "created_at": n.created_at.isoformat(),
            }
            for n in s.scalars(q).all()
        ]


@router.post("/notifications/read", tags=["notifications"])
def mark_read(ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        for n in s.scalars(
            select(Notification).where(Notification.user_id == ident.sub, Notification.read.is_(False))
        ):
            n.read = True
    return {"ok": True}


@router.get("/me", tags=["me"])
def me(ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        u = s.get(User, ident.sub)
        return {
            "id": ident.sub if not ident.anonymous else "anonymous",
            "anonymous": ident.anonymous,
            "email": ident.email,
            "preferences": (u.preferences if u else {}) or {},
        }


@router.patch("/me/preferences", tags=["me"])
def set_prefs(body: PreferencesIn, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    with session_scope() as s:
        u = s.get(User, ident.sub)
        assert u is not None
        prefs = dict(u.preferences or {})
        prefs.update({k: v for k, v in body.model_dump(mode="json").items() if v is not None})
        u.preferences = prefs
    return prefs


# ---------------------------------------------------------------- company data


@router.get("/companies/{ticker}", tags=["companies"])
async def company(
    ticker: str, ident: Identity = Depends(current_identity), fixture: bool | None = None
) -> dict[str, Any]:
    from app.services import company_profile

    s = get_settings()
    use_fixture = s.fixture_mode or bool(fixture)
    return await asyncio.to_thread(company_profile, ticker.upper(), use_fixture)


@router.get("/track-record", tags=["companies"])
def track_record(ticker: str | None = None, ident: Identity = Depends(current_identity)) -> dict[str, Any]:
    from app.services import track_record_summary

    return track_record_summary(ticker.upper() if ticker else None)


@router.get("/calendar.ics", tags=["watchlist"])
def calendar_ics(ident: Identity = Depends(current_identity)) -> Response:
    from app.services import next_event_for

    with session_scope() as s:
        tickers = [
            i.ticker for i in s.scalars(select(WatchlistItem).where(WatchlistItem.user_id == ident.sub)).all()
        ]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Earnings Intel//EN", "CALSCALE:GREGORIAN"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for t in tickers:
        ev = next_event_for(t)
        if not ev or not ev.get("date"):
            continue
        d = date.fromisoformat(ev["date"])
        lines += [
            "BEGIN:VEVENT",
            f"UID:{t}-{d.isoformat()}@earnings-intel",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{t} earnings ({(ev.get('time') or 'time tbc').upper()}, {ev.get('status', 'estimated')})",
            "DESCRIPTION:Date status is shown in the summary. Research only, not advice.",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response(
        "\r\n".join(lines) + "\r\n",
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="earnings.ics"'},
    )


def _dump(o: Any) -> str:
    return json.dumps(o, default=str)


__all__ = ["Prediction", "router"]
