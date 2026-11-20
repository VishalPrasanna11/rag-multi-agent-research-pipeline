"""Background jobs (Celery). Schedules run in America/New_York.

Reliability rules:
- Every scheduled job is idempotent per schedule slot (JobLock key), so a duplicated beat or a redelivered
  message never double-applies work.
- Retries with exponential backoff; after the last retry the call is written to `dead_letters`.
- In fixture mode (no LLM key) the jobs no-op except prediction scoring.

Run:  celery -A app.jobs worker --beat --loglevel=INFO --concurrency=2
(exactly one worker process runs --beat; scaling workers beyond one must drop --beat on the extras.)
"""

from __future__ import annotations

import functools
import ssl
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import (
    ConsensusSnapshot,
    DeadLetter,
    JobLock,
    Notification,
    Prediction,
    Run,
    SeenFiling,
    User,
    WatchlistItem,
    init_db,
    session_scope,
    utcnow,
)
from app.logging import configure_logging, get_logger, redact
from app.market import NY, reaction

log = get_logger("jobs")
_s = get_settings()

celery_app = Celery("earnings_intel", broker=_s.REDIS_URL or "memory://", backend=None)
_tls = (
    {"ssl_cert_reqs": ssl.CERT_NONE} if _s.REDIS_URL.startswith("rediss://") else None
)  # Heroku KV: self-signed
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="America/New_York",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    broker_use_ssl=_tls,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "snapshot-consensus": {"task": "jobs.snapshot_consensus", "schedule": crontab(hour=7, minute=5)},
        "poll-earnings-filings": {
            "task": "jobs.poll_filings",
            "schedule": crontab(minute="*/2", hour="6-21", day_of_week="1-5"),
        },
        "score-predictions": {
            "task": "jobs.score_predictions",
            "schedule": crontab(hour=18, minute=30, day_of_week="1-5"),
        },
        "prune-cache": {"task": "jobs.prune_cache", "schedule": crontab(hour=3, minute=15)},
    },
)


@celery_app.on_after_configure.connect  # type: ignore[untyped-decorator]
def _setup(**_: Any) -> None:
    configure_logging(_s.LOG_LEVEL, json=True)
    init_db()


def claim(key: str) -> bool:
    """Idempotency: True the first time a key is seen, False afterwards."""
    try:
        with session_scope() as s:
            s.add(JobLock(key=key, status="done"))
        return True
    except IntegrityError:
        return False


def reliable(name: str, max_retries: int = 3) -> Callable[[Callable[..., Any]], Any]:
    def deco(fn: Callable[..., Any]) -> Any:
        @celery_app.task(name=f"jobs.{name}", bind=True, max_retries=max_retries)
        @functools.wraps(fn)
        def task(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if self.request.retries >= max_retries:
                    with session_scope() as s:
                        s.add(
                            DeadLetter(
                                task=name,
                                args={"args": args, "kwargs": kwargs},
                                error=redact(repr(e))[:4000],
                                attempts=self.request.retries + 1,
                            )
                        )
                    log.error("job_dead_lettered", job=name, error=redact(repr(e)))
                    return {"status": "dead_lettered"}
                raise self.retry(exc=e, countdown=min(600, 30 * 2**self.request.retries)) from e

        return task

    return deco


def _tickers() -> list[str]:
    with session_scope() as s:
        return sorted({t for (t,) in s.execute(select(WatchlistItem.ticker)).all()})


# ---------------------------------------------------------------- jobs


@reliable("snapshot_consensus")
def snapshot_consensus(today: str | None = None) -> dict[str, Any]:
    """Daily consensus snapshot for each watched ticker's next event -> our own revision history."""
    if get_settings().fixture_mode:
        return {"status": "skipped", "reason": "fixture mode"}
    from app.providers import ProviderError, get_providers

    d = date.fromisoformat(today) if today else datetime.now(NY).date()
    if not claim(f"snapshot:{d}"):
        return {"status": "already_done"}
    prov = get_providers(False)
    n = 0
    for t in _tickers():
        try:
            rows = prov.earnings_history(t).data
        except ProviderError as e:
            log.warning("snapshot_skip", ticker=t, error=str(e))
            continue
        nxt = sorted(
            [r for r in rows if r.get("date") and r["date"] > d.isoformat() and r.get("eps_act") is None],
            key=lambda r: r["date"],
        )
        if not nxt:
            continue
        r = nxt[0]
        with session_scope() as s:
            s.merge(
                ConsensusSnapshot(
                    ticker=t,
                    event_date=date.fromisoformat(r["date"]),
                    snapshot_date=d,
                    eps_mean=r.get("eps_est"),
                    revenue_mean=r.get("rev_est"),
                    source="fmp",
                )
            )
        n += 1
    return {"status": "ok", "snapshots": n}


@reliable("poll_filings")
def poll_filings() -> dict[str, Any]:
    """Event-driven live mode: a new 8-K Item 2.02 for a watched ticker -> notify watchers; auto-run if enabled."""
    s = get_settings()
    if s.fixture_mode:
        return {"status": "skipped", "reason": "fixture mode"}
    from app.providers import ProviderError, get_providers

    prov = get_providers(False)
    new = 0
    since = (datetime.now(NY).date() - timedelta(days=3)).isoformat()
    for t in _tickers():
        try:
            cik = prov.resolve(t).data["cik"]
            filings = prov.submissions(cik).data["filings"]
        except ProviderError as e:
            log.warning("poll_skip", ticker=t, error=str(e))
            continue
        for f in filings:
            if f["form"] != "8-K" or "2.02" not in (f["items"] or "") or f["filing_date"] < since:
                continue
            try:
                with session_scope() as sess:
                    sess.add(
                        SeenFiling(
                            accession=f["accession"],
                            ticker=t,
                            form="8-K",
                            items=f["items"],
                            accepted_at=datetime.fromisoformat(f["accepted"].replace("Z", "+00:00"))
                            if f.get("accepted")
                            else None,
                        )
                    )
            except IntegrityError:
                continue  # already processed
            new += 1
            process_filing.delay(t, f["accession"], f["filing_date"])
    return {"status": "ok", "new_filings": new}


@reliable("process_filing")
def process_filing(ticker: str, accession: str, filing_date: str) -> dict[str, Any]:
    from app import engine

    with session_scope() as s:
        watchers = s.execute(
            select(WatchlistItem.user_id, WatchlistItem.auto_run, User.preferences)
            .join(User, User.id == WatchlistItem.user_id)
            .where(WatchlistItem.ticker == ticker)
        ).all()
    runs = 0
    for user_id, auto_run, prefs in watchers:
        if (prefs or {}).get("alerts_enabled", True) is False:
            continue
        report_hint: dict[str, Any] = {"accession": accession}
        if auto_run and claim(f"autorun:{user_id}:{accession}"):
            run = engine.create_run(
                user_id, ticker, "post_only", None, None, date.fromisoformat(filing_date), False
            )
            status = engine.execute(run.id)
            with session_scope() as s:
                r = s.get(Run, run.id)
                report_hint.update(run_id=run.id, report_id=r.report_id if r else None, status=status)
            runs += 1
        try:
            with session_scope() as s:
                s.add(
                    Notification(
                        user_id=user_id,
                        kind="earnings_8k",
                        ticker=ticker,
                        title=f"{ticker} filed earnings results (8-K Item 2.02, {filing_date})",
                        body=report_hint,
                        dedupe_key=f"8k:{accession}",
                    )
                )
        except IntegrityError:
            pass
    return {"status": "ok", "watchers": len(watchers), "auto_runs": runs}


@reliable("score_predictions")
def score_predictions() -> dict[str, Any]:
    """Score stored forecasts once the post-event session has closed: outcome = 1 if the day-1 move was up."""
    from app.providers import ProviderError, get_providers

    prov = get_providers(get_settings().fixture_mode)
    today = datetime.now(NY).date()
    scored = 0
    with session_scope() as s:
        pending = s.scalars(
            select(Prediction).where(
                Prediction.scored_at.is_(None), Prediction.event_date <= today - timedelta(days=2)
            )
        ).all()
        items = [(p.id, p.ticker, p.event_date, p.kind) for p in pending]
    for pid, ticker, ev, kind in items:
        try:
            bars = sorted(
                prov.prices(ticker, ev - timedelta(days=10), ev + timedelta(days=10)).data,
                key=lambda b: b["day"],
            )
            hist = {r["date"]: r.get("time") for r in prov.earnings_history(ticker).data}
        except ProviderError:
            continue
        rx = reaction(bars, ev, hist.get(ev.isoformat()) or "amc")
        if not rx:
            continue
        with session_scope() as s:
            p = s.get(Prediction, pid)
            if p is None or p.scored_at is not None:
                continue
            p.outcome = (1.0 if rx.day1_pct > 0 else 0.0) if kind == "p_up_day1" else abs(rx.day1_pct)
            p.scored_at = utcnow()
        scored += 1
    return {"status": "ok", "scored": scored}


@reliable("prune_cache")
def prune_cache() -> dict[str, Any]:
    from sqlalchemy import delete

    from app.db import ProviderCache

    with session_scope() as s:
        res = s.execute(
            delete(ProviderCache).where(ProviderCache.expires_at < datetime.now(UTC) - timedelta(days=1))
        )
        return {"status": "ok", "deleted": getattr(res, "rowcount", 0)}
