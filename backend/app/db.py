"""SQLAlchemy models + session. Postgres in production, SQLite fallback (non-durable) otherwise."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TZDateTime(TypeDecorator[datetime]):
    """Always timezone-aware UTC in Python (SQLite drops tzinfo on the way back)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return _aware(value)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------- app data


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # JWT sub or anon:<uuid>
    email: Mapped[str | None] = mapped_column(String(320))
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "ticker"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(12))
    auto_run: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    query: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[datetime] = mapped_column(TZDateTime())
    event_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # queued|running|awaiting_input|awaiting_review|completed|failed|cancelled
    fixture: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    report_id: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # clarification / review prompt
    inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # e.g. uploaded transcript
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    agent: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class ReportRow(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    review_status: Mapped[str] = mapped_column(String(24), default="pending")
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSON)  # schemas.Report
    schema_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(24))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class AuditLog(Base):
    """Append-only. Never updated or deleted by the application."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(128))
    rating: Mapped[str] = mapped_column(String(8))
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    section_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class ShareToken(Base):
    __tablename__ = "share_tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(String(128))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    ticker: Mapped[str | None] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    report_id: Mapped[str | None] = mapped_column(String(40), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128))
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    event_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(32))  # p_up_day1 | abs_move_pct | surprise_positive
    value: Mapped[float] = mapped_column(Float)
    baseline: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default="live")  # live | backtest
    made_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    outcome: Mapped[float | None] = mapped_column(Float)
    scored_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    __table_args__ = (Index("ix_pred_unscored", "scored_at", "event_date"),)


class DeadLetter(Base):
    __tablename__ = "dead_letters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(String(120))
    args: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class JobLock(Base):
    """Idempotency keys for scheduled/event jobs."""

    __tablename__ = "job_runs"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="done")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


# ---------------------------------------------------------------- market data (MarketRepo)


class PriceBar(Base):
    __tablename__ = "price_bars"
    ticker: Mapped[str] = mapped_column(String(12), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)  # split-adjusted
    volume: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))
    retrieved_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class ConsensusSnapshot(Base):
    """Daily snapshot of consensus for the next unreported quarter -> our own revision history."""

    __tablename__ = "consensus_snapshots"
    ticker: Mapped[str] = mapped_column(String(12), primary_key=True)
    event_date: Mapped[date] = mapped_column(Date, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    eps_mean: Mapped[float | None] = mapped_column(Float)
    revenue_mean: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))


class ProviderCache(Base):
    __tablename__ = "provider_cache"
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), index=True)


class SeenFiling(Base):
    __tablename__ = "seen_filings"
    accession: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    form: Mapped[str] = mapped_column(String(16))
    items: Mapped[str | None] = mapped_column(String(64))
    accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    processed: Mapped[bool] = mapped_column(Boolean, default=False)


class DocChunk(Base):
    """Fallback vector store (cosine in Python) + chunk registry for any VectorStore."""

    __tablename__ = "doc_chunks"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    doc_type: Mapped[str] = mapped_column(String(16))
    fiscal_period: Mapped[str | None] = mapped_column(String(16))
    section: Mapped[str | None] = mapped_column(String(64))
    accession: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(500))
    as_of: Mapped[datetime] = mapped_column(TZDateTime())
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[Any] | None] = mapped_column(JSON)
    __table_args__ = (Index("ix_chunk_filter", "ticker", "doc_type", "fiscal_period"),)


# ---------------------------------------------------------------- engine/session


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    url = s.database_url
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        if url != "sqlite://":
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_size=5, max_overflow=5)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragma(dbapi_conn: Any, _: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    sess = _sessionmaker()()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def init_db() -> None:
    """Bring the schema to head. Tests use create_all; every other environment runs Alembic migrations
    (SQLite fallback and Postgres alike), so local and production schemas never drift."""
    s = get_settings()
    if s.ENV == "test":
        Base.metadata.create_all(get_engine())
        return
    from alembic import command
    from alembic.config import Config

    from app.config import ROOT

    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    with get_engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def audit(sess: Session, actor: str, action: str, subject_type: str, subject_id: str, **detail: Any) -> None:
    from app.logging import request_id_var

    sess.add(
        AuditLog(
            actor_id=actor,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            detail=detail,
            request_id=request_id_var.get(),
        )
    )
