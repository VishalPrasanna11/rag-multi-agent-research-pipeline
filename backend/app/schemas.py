"""Typed domain + API schemas (schema_version in app.config.SCHEMA_VERSION)."""

from __future__ import annotations

import re
from datetime import date, datetime
from datetime import date as Date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DISCLAIMER = (
    "Research only — not investment advice. This output is generated from public data and may be "
    "incomplete, delayed or wrong. It does not consider your objectives or circumstances. "
    "Verify every figure against the cited source before acting."
)

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class RunMode(StrEnum):
    FULL_CYCLE = "full_cycle"
    PRE_ONLY = "pre_only"
    POST_ONLY = "post_only"
    QUESTION = "question"


class SourceKind(StrEnum):
    SEC_XBRL = "sec_xbrl"
    SEC_FILING = "sec_filing"
    FMP = "fmp"
    YFINANCE = "yfinance"
    TAVILY = "tavily"
    FRED = "fred"
    USER_UPLOAD = "user_upload"
    DERIVED = "derived"
    FIXTURE = "fixture"


class Citation(Model):
    id: str
    source: SourceKind
    title: str
    url: str | None = None
    as_of: datetime
    retrieved_at: datetime
    locator: str | None = None  # accession / XBRL tag / section / span
    license_note: str | None = None


class Fact(Model):
    key: str
    label: str
    value: float | str | None
    unit: str = ""  # "USD", "USD/share", "%", "x", "days", "count", ""
    period: str | None = None
    as_of: datetime
    citation_id: str
    derived_from: list[str] = Field(default_factory=list)
    formula: str | None = None
    status: Literal["ok", "unavailable"] = "ok"
    needs: str | None = None  # for unavailable facts: what would make it available

    def display(self) -> str:
        return format_value(self.value, self.unit)


def format_value(value: float | str | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, str):
        return value
    v = float(value)
    if unit == "USD":
        a = abs(v)
        if a >= 1e9:
            return f"${v / 1e9:,.2f}B"
        if a >= 1e6:
            return f"${v / 1e6:,.1f}M"
        return f"${v:,.0f}"
    if unit == "USD/share":
        return f"${v:,.2f}"
    if unit == "%":
        return f"{v:+.1f}%" if abs(v) < 1000 else f"{v:,.0f}%"
    if unit == "pct":  # level percentages (margins) without sign
        return f"{v:.1f}%"
    if unit == "x":
        return f"{v:,.1f}x"
    if unit == "days":
        return f"{v:,.0f} days"
    if unit == "count":
        return f"{v:,.0f}"
    if unit == "prob":
        return f"{v * 100:.0f}%"
    return f"{v:,.2f}"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(Model):
    level: ConfidenceLevel
    method: Literal["evidence_coverage", "base_rate", "calibrated"] = "evidence_coverage"
    score: float | None = Field(default=None, ge=0, le=1)
    explanation: str
    drivers: list[str] = Field(default_factory=list)


class Section(Model):
    id: str
    title: str
    markdown: str  # rendered: every number is wrapped as [[fact:key|display]]
    citations: list[str] = Field(default_factory=list)


class AgentStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class AgentOutput(Model):
    agent: str
    status: AgentStatus
    sections: list[Section] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    confidence: Confidence
    timing_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str | None = None


class Scenario(Model):
    name: Literal["bull", "base", "bear"]
    probability: float = Field(ge=0, le=1)
    probability_method: Literal["base_rate", "heuristic", "calibrated"]
    base_rate_n: int | None = None
    narrative: str
    evidence: list[str] = Field(default_factory=list)  # fact keys
    move_range_pct: tuple[float, float] | None = None


class Freshness(Model):
    source: str
    as_of: datetime | None


class DecisionBrief(Model):
    headline: str
    setup: list[str]
    scenarios: list[Scenario]
    what_would_change_view: list[str]
    questions_for_call: list[str]
    key_risks: list[str]
    unknowns: list[str]
    freshness: list[Freshness]
    confidence: Confidence
    disclaimer: str = DISCLAIMER
    validated: bool = False  # True only for claims the back-test supports
    validation_note: str = ""


class TimeOfDay(StrEnum):
    BMO = "bmo"
    AMC = "amc"
    DMH = "dmh"
    UNKNOWN = "unknown"


class EarningsEvent(Model):
    ticker: str
    cik: str | None = None
    company: str | None = None
    fiscal_period: str | None = None  # e.g. "FY2027 Q3"
    date: Date | None = None
    time_of_day: TimeOfDay = TimeOfDay.UNKNOWN
    date_status: Literal["confirmed", "estimated", "reported", "unknown"] = "unknown"
    citation_id: str | None = None


class CritiqueIssue(Model):
    kind: Literal[
        "uncited_number", "stale_fact", "contradiction", "unsupported_claim", "missing", "safety", "other"
    ]
    severity: Literal["high", "medium", "low"]
    agent: str | None = None
    detail: str


class CritiqueRound(Model):
    round: int
    issues: list[CritiqueIssue]
    action: Literal["refined", "accepted", "budget_stop"]
    tokens: int = 0


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class Report(Model):
    id: str
    run_id: str
    ticker: str
    mode: RunMode
    query: str | None = None
    as_of: datetime
    created_at: datetime
    event: EarningsEvent
    brief: DecisionBrief | None = None
    agents: list[AgentOutput] = Field(default_factory=list)
    facts: dict[str, Fact] = Field(default_factory=dict)
    citations: dict[str, Citation] = Field(default_factory=dict)
    critique_history: list[CritiqueRound] = Field(default_factory=list)
    review_status: Literal["pending", "approved", "rejected", "changes_requested", "not_required"] = "pending"
    fixture: bool = False
    notices: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)  # document excerpts the writers saw (auditability)
    cost_usd: float = 0.0
    disclaimer: str = DISCLAIMER
    schema_version: str


# ---------------------------------------------------------------- API requests


class RunCreate(Model):
    ticker: str = Field(min_length=1, max_length=10)
    mode: RunMode = RunMode.FULL_CYCLE
    query: str | None = Field(default=None, max_length=2000)
    as_of: date | None = None  # research "as of" this date (back-test / historical)
    event_date: date | None = None
    fixture: bool | None = None
    transcript_text: str | None = Field(default=None, max_length=300_000)
    transcript_label: str | None = Field(default=None, max_length=200)
    debug_plant: str | None = Field(
        default=None, max_length=500, description="eval hook; ignored in production"
    )
    debug_fail: list[str] | None = Field(default=None, description="eval hook: simulate provider outages")

    @field_validator("ticker")
    @classmethod
    def _norm_ticker(cls, v: str) -> str:
        v = v.strip().upper().replace("$", "")
        if not TICKER_RE.match(v):
            raise ValueError("ticker must look like a US symbol, e.g. NVDA or BRK.B")
        return v


class RunResume(Model):
    answer: str | None = None  # for clarification
    decision: ReviewDecision | None = None
    notes: str | None = Field(default=None, max_length=4000)


class RunSummary(Model):
    id: str
    ticker: str
    mode: RunMode
    status: str
    query: str | None
    created_at: datetime
    finished_at: datetime | None
    report_id: str | None
    fixture: bool
    cost_usd: float
    review_status: str | None = None


StreamEventType = Literal[
    "run.started",
    "agent.started",
    "agent.progress",
    "fact.added",
    "section.delta",
    "agent.completed",
    "critique.round",
    "clarification.required",
    "review.required",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "heartbeat",
]


class StreamEvent(Model):
    run_id: str
    seq: int
    ts: datetime
    type: StreamEventType
    agent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WatchlistItemIn(Model):
    ticker: str

    @field_validator("ticker")
    @classmethod
    def _norm(cls, v: str) -> str:
        return RunCreate._norm_ticker(v)


class FeedbackIn(Model):
    rating: Literal["up", "down"]
    reasons: list[str] = Field(default_factory=list, max_length=8)
    comment: str | None = Field(default=None, max_length=2000)
    section_id: str | None = None


class PreferencesIn(Model):
    density: Literal["comfortable", "compact"] | None = None
    theme: Literal["system", "light", "dark"] | None = None
    default_mode: RunMode | None = None
    alerts_enabled: bool | None = None
