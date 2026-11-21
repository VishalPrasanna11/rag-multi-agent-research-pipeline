"""Run engine: LangGraph graph + persisted event log + interrupts (clarification, human review).

Graph:  scope -> gather -> analyze -> brief -> critique --(high issues & budget)--> refine -> critique
                                                        \\-> save -> review (interrupt) -> finalize
Events are appended to `run_events` with a per-run sequence; the SSE endpoint replays and tails them,
so a reconnecting client resumes with Last-Event-ID and never misses or duplicates an event.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import func, select

from app import analysts as A
from app.config import SCHEMA_VERSION, get_settings
from app.db import Prediction, ReportRow, Run, RunEvent, User, audit, new_id, session_scope, utcnow
from app.facts import FactStore
from app.gather import GatherCtx, attach_transcript, cutoff_dt, gather_all
from app.llm import LLMRouter
from app.logging import get_logger, redact, run_id_var
from app.market import NY
from app.observability import span
from app.providers import NotFound, Providers, get_providers
from app.schemas import (
    AgentOutput,
    AgentStatus,
    CritiqueIssue,
    CritiqueRound,
    DecisionBrief,
    EarningsEvent,
    Report,
    RunMode,
    Section,
)

log = get_logger("engine")

TERMINAL = {"run.completed", "run.failed", "run.cancelled"}
AGENT_LABEL = {
    "pre_earnings": "Pre-earnings",
    "live_earnings": "Live earnings",
    "transcript": "Transcript & filings",
    "post_earnings": "Post-earnings",
    "synthesis": "Synthesis",
    "data": "Data",
    "expectations": "Pre-earnings",
    "market": "Pre-earnings",
    "fundamentals": "Live earnings",
    "filings": "Transcript & filings",
    "context": "Pre-earnings",
    "critique": "Critique",
    "review": "Review",
}


ADVICE_NOTICE = (
    "You asked for a personal investment decision. This tool can't tell you what to buy or sell; it gives "
    "research on the setup, risks and evidence so you can make your own decision."
)


class RunCancelled(Exception):
    pass


class ScopeRefused(Exception):
    pass


# ---------------------------------------------------------------- event log


class EventLog:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._lock = threading.Lock()
        with session_scope() as s:
            self.seq = int(
                s.scalar(select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id))
                or 0
            )

    def emit(self, type_: str, agent: str | None = None, **payload: Any) -> int:
        with self._lock:  # analysts emit from parallel threads; seq must stay strictly increasing
            self.seq += 1
            seq = self.seq
            with session_scope() as s:
                s.add(
                    RunEvent(
                        run_id=self.run_id, seq=seq, type=type_, agent=agent, payload=payload, ts=utcnow()
                    )
                )
        return seq


def is_cancelled(run_id: str) -> bool:
    with session_scope() as s:
        r = s.get(Run, run_id)
        return bool(r and r.cancel_requested)


# ---------------------------------------------------------------- services & state


@dataclass
class Services:
    providers: Providers
    llm: LLMRouter
    events: EventLog
    run_id: str
    ctx: GatherCtx | None = None
    started: dict[str, float] = field(default_factory=dict)

    def progress(self, agent: str, message: str) -> None:
        if is_cancelled(self.run_id):
            raise RunCancelled()
        a = AGENT_LABEL.get(agent, agent)
        self.events.emit("agent.progress", a, message=message)


class RunState(TypedDict, total=False):
    run_id: str
    ticker: str
    mode: str
    query: str | None
    cutoff: str
    event_hint: str | None
    intent: str
    gathered: dict[str, Any]
    sections: dict[str, list[dict[str, Any]]]
    agents: list[dict[str, Any]]
    brief: dict[str, Any] | None
    answer: dict[str, Any] | None
    critique: list[dict[str, Any]]
    feedback: dict[str, list[str]]
    report_id: str | None
    review: dict[str, Any] | None


def _svc(config: RunnableConfig) -> Services:
    return config["configurable"]["svc"]  # type: ignore[no-any-return]


def _restore_ctx(state: RunState, svc: Services) -> GatherCtx:
    if svc.ctx is not None:
        return svc.ctx
    g = state["gathered"]
    cutoff = date.fromisoformat(state["cutoff"])
    store = FactStore.load(cutoff_dt(cutoff), g["store"])
    ctx = GatherCtx(svc.providers, store, state["ticker"], state["mode"], cutoff, progress=svc.progress)
    ctx.cik, ctx.company = g.get("cik"), g.get("company")
    ctx.event = EarningsEvent.model_validate(g["event"]) if g.get("event") else None
    ctx.docs, ctx.doc_cites, ctx.unknowns = g.get("docs", {}), g.get("doc_cites", {}), g.get("unknowns", [])
    ctx._reactions = g.get("reactions", [])  # type: ignore[attr-defined]
    svc.ctx = ctx
    return ctx


def _dump_ctx(ctx: GatherCtx) -> dict[str, Any]:
    return {
        "store": ctx.store.dump(),
        "cik": ctx.cik,
        "company": ctx.company,
        "event": ctx.event.model_dump(mode="json") if ctx.event else None,
        "docs": {k: v[: (250000 if k == "transcript" else 60000)] for k, v in ctx.docs.items()},
        "doc_cites": ctx.doc_cites,
        "unknowns": ctx.unknowns,
        "reactions": getattr(ctx, "_reactions", []),
    }


# ---------------------------------------------------------------- nodes


def scope_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    svc = _svc(config)
    intent = A.classify_query(state.get("query"))
    if intent == "off_topic":
        raise ScopeRefused(
            "This assistant only answers questions about public companies' earnings and filings."
        )
    if intent == "advice":
        svc.events.emit(
            "agent.progress",
            "Synthesis",
            message=(
                "This looks like a request for personal investment advice. I can't tell you what to buy or sell, "
                "but here is research on the setup, risks and evidence to support your own decision."
            ),
        )
    ticker = state["ticker"]
    try:
        svc.providers.resolve(ticker)
    except NotFound:
        options = _suggest_tickers(svc.providers, ticker)
        answer = interrupt(
            {
                "kind": "clarification",
                "question": f"I couldn't find '{ticker}' among SEC-registered US filers. Did you mean one of these?",
                "options": options,
            }
        )
        ticker = str(answer).strip().upper()
        svc.providers.resolve(ticker)  # raises NotFound -> run.failed with a clear message
    return {"intent": intent, "ticker": ticker}


def _suggest_tickers(providers: Providers, raw: str) -> list[str]:
    import difflib
    import json

    from app.providers import FIXTURE_DIR

    pool: list[str] = []
    try:
        if providers.name == "fixture":
            pool = [p.stem for p in FIXTURE_DIR.glob("*.json")]
        else:
            from app.providers import _cache_get

            data = _cache_get("sec|https://www.sec.gov/files/company_tickers.json") or {}
            pool = [str(r["ticker"]) for r in data.values()] if isinstance(data, dict) else []
            if not pool:
                pool = json.loads("[]")
    except Exception:
        pool = []
    return difflib.get_close_matches(raw.upper(), pool, n=4, cutoff=0.5) or pool[:4]


def gather_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    svc = _svc(config)
    cutoff = date.fromisoformat(state["cutoff"])
    ctx = GatherCtx(
        svc.providers,
        FactStore(cutoff_dt(cutoff)),
        state["ticker"],
        state["mode"],
        cutoff,
        event_hint=date.fromisoformat(str(state["event_hint"])) if state.get("event_hint") else None,
        progress=svc.progress,
    )
    svc.ctx = ctx
    svc.events.emit("agent.started", "Data", message="Gathering cited facts")
    gather_all(ctx, lambda: is_cancelled(svc.run_id))
    inputs = _run_inputs(state["run_id"])
    if inputs.get("transcript_text"):
        attach_transcript(ctx, inputs["transcript_text"], inputs.get("transcript_label"))
    if ctx.docs.get("transcript"):
        svc.progress("filings", "Analysing the uploaded transcript: prepared remarks vs Q&A")
        tinfo = A.transcript_agent(ctx, svc.llm)
        svc.events.emit(
            "agent.progress",
            "Transcript & filings",
            message=f"Transcript: {tinfo.get('themes', 0)} question themes, "
            f"{tinfo.get('evasive', 0)} verified non-answers",
        )
    if ctx.docs.get("press_release"):
        svc.progress("filings", "Extracting figures and guidance from the press release (quotes verified)")
        info = A.documents_agent(ctx, svc.llm)
        svc.events.emit(
            "agent.progress",
            "Transcript & filings",
            message=f"Verified {info.get('press_release', {}).get('kept', 0)} figures from the release; "
            f"dropped {info.get('press_release', {}).get('dropped', 0)} that could not be matched to a quote",
        )
    ok = len(ctx.store.ok_keys())
    na = len(ctx.store.facts) - ok
    svc.events.emit(
        "fact.added",
        "Data",
        count=ok,
        unavailable=na,
        event=ctx.event.model_dump(mode="json") if ctx.event else None,
    )
    svc.events.emit("agent.completed", "Data", message=f"{ok} cited facts, {na} marked unavailable")
    return {"gathered": _dump_ctx(ctx)}


def _run_inputs(run_id: str) -> dict[str, Any]:
    with session_scope() as s:
        r = s.get(Run, run_id)
        return dict(r.inputs or {}) if r else {}


def analyze_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    svc = _svc(config)
    ctx = _restore_ctx(state, svc)
    agents = A.MODE_AGENTS[state["mode"]]
    sections: dict[str, list[dict[str, Any]]] = {}
    outputs: list[dict[str, Any]] = []

    def run_agent(agent: str) -> tuple[str, list[Section], str, int]:
        t0 = time.perf_counter()
        svc.events.emit("agent.started", AGENT_LABEL[agent], message="Writing from cited facts")
        drafts, how = A.write_sections(ctx, svc.llm, agent, state.get("query"), state.get("feedback"))
        final = []
        for d in drafts:
            sec, problems = A.finalize_section(d, ctx.store)
            final.append(sec)
            svc.events.emit("section.delta", AGENT_LABEL[agent], section=sec.model_dump(mode="json"))
            for p in problems:
                ctx.unknowns.append(p)
        return agent, final, how, int((time.perf_counter() - t0) * 1000)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for agent, final, how, ms in ex.map(run_agent, agents):
            if is_cancelled(svc.run_id):
                raise RunCancelled()
            out = A.agent_output(agent, final, ctx, state["mode"], how, ms)
            sections[agent] = [s.model_dump(mode="json") for s in final]
            outputs.append(out.model_dump(mode="json"))
            svc.events.emit(
                "agent.completed",
                AGENT_LABEL[agent],
                status=out.status,
                timing_ms=ms,
                message=f"{len(final)} section(s), {len(out.facts)} facts cited"
                + ("" if how == "llm" else f" ({how})"),
            )
    return {"sections": sections, "agents": outputs, "gathered": _dump_ctx(ctx)}


def brief_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    svc = _svc(config)
    ctx = _restore_ctx(state, svc)
    svc.events.emit("agent.started", "Synthesis", message="Writing the decision brief")
    flat = [Section.model_validate(s) for secs in state["sections"].values() for s in secs]
    brief, answer, how = A.write_brief(ctx, svc.llm, state.get("query"), flat, state["mode"])
    svc.events.emit(
        "section.delta",
        "Synthesis",
        brief=brief.model_dump(mode="json"),
        answer=answer.model_dump(mode="json") if answer else None,
    )
    svc.events.emit(
        "agent.completed", "Synthesis", message="Decision brief ready" + ("" if how == "llm" else f" ({how})")
    )
    return {
        "brief": brief.model_dump(mode="json"),
        "answer": answer.model_dump(mode="json") if answer else None,
    }


def _all_sections(state: RunState) -> list[Section]:
    out = [Section.model_validate(s) for secs in state["sections"].values() for s in secs]
    if state.get("answer"):
        out.append(Section.model_validate(state["answer"]))
    return out


def critique_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    svc = _svc(config)
    ctx = _restore_ctx(state, svc)
    rnd = len(state.get("critique") or []) + 1
    plant = _run_inputs(state["run_id"]).get("debug_plant") if rnd == 1 else None
    if plant and state.get("sections"):  # eval hook: the critique must catch and remove this sentence
        first_agent = next(iter(state["sections"]))
        secs = [dict(x) for x in state["sections"][first_agent]]
        secs[0]["markdown"] = secs[0]["markdown"] + "\n\n" + plant
        state = {**state, "sections": {**state["sections"], first_agent: secs}}
    svc.events.emit("agent.progress", "Critique", message=f"Checking grounding and consistency (round {rnd})")
    sections = _all_sections(state)
    brief = DecisionBrief.model_validate(state["brief"]) if state.get("brief") else None
    issues = A.deterministic_checks(sections, brief, ctx.store)
    tokens_before = svc.llm.usage.tokens_in + svc.llm.usage.tokens_out
    if svc.llm.enabled and svc.llm.remaining_cost() > 0.03:
        issues += A.llm_critique(svc.llm, sections, ctx.store, A.critic_evidence(ctx, state.get("query")))
    # Only substantive defects trigger a rewrite; "missing" suggestions are kept for the reviewer.
    high = [i for i in issues if i.severity == "high" and i.kind in A.REFINE_KINDS]
    max_rounds = get_settings().MAX_CRITIQUE_ROUNDS
    can_refine = bool(high) and rnd <= max_rounds and svc.llm.enabled and svc.llm.remaining_cost() > 0.05
    action = "refined" if can_refine else ("budget_stop" if high and rnd <= max_rounds else "accepted")
    tokens = svc.llm.usage.tokens_in + svc.llm.usage.tokens_out - tokens_before
    cr = CritiqueRound(round=rnd, issues=issues, action=action, tokens=tokens)  # type: ignore[arg-type]
    svc.events.emit(
        "critique.round", "Critique", round=rnd, issues=[i.model_dump() for i in issues], action=action
    )
    feedback: dict[str, list[str]] = {}
    for i in high:
        feedback.setdefault(i.agent or "", []).append(f"{i.kind}: {i.detail}")
    out: dict[str, Any] = {
        "critique": [*(state.get("critique") or []), cr.model_dump(mode="json")],
        "feedback": feedback,
    }
    if plant:
        out["sections"] = state["sections"]
    return out


def after_critique(state: RunState) -> str:
    last = (state.get("critique") or [{}])[-1]
    return "refine" if last.get("action") == "refined" else "save"


def refine_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    svc = _svc(config)
    ctx = _restore_ctx(state, svc)
    fb = state.get("feedback") or {}
    sections = dict(state["sections"])
    todo = []
    for agent, plan in A.SECTION_PLAN.items():
        ids = {sid for sid, *_ in plan}
        if agent in sections and ids & set(fb):
            todo.append((agent, {k: v for k, v in fb.items() if k in ids}))

    def redo(job: tuple[str, dict[str, list[str]]]) -> tuple[str, list[dict[str, Any]]]:
        agent, agent_fb = job
        svc.events.emit("agent.progress", AGENT_LABEL[agent], message="Revising after critique")
        drafts, _ = A.write_sections(ctx, svc.llm, agent, state.get("query"), agent_fb)
        return agent, [A.finalize_section(d, ctx.store)[0].model_dump(mode="json") for d in drafts]

    touched = []
    with ThreadPoolExecutor(max_workers=4) as ex:  # revise flagged agents in parallel
        for agent, secs in ex.map(redo, todo):
            sections[agent] = secs
            touched.append(agent)
    out: dict[str, Any] = {"sections": sections}
    if "brief" in fb:
        brief, answer, _ = A.write_brief(
            ctx,
            svc.llm,
            state.get("query"),
            [Section.model_validate(s) for v in sections.values() for s in v],
            state["mode"],
        )
        out["brief"] = brief.model_dump(mode="json")
        out["answer"] = answer.model_dump(mode="json") if answer else None
        touched.append("synthesis")
    if not touched:  # issues pointed at sections we cannot rewrite -> stop looping
        crit = list(state.get("critique") or [])
        crit[-1] = {**crit[-1], "action": "accepted"}
        out["critique"] = crit
    return out


def _evidence(ctx: GatherCtx, query: str | None) -> dict[str, str]:
    """Excerpts given to writers/critic, persisted for reviewers and evaluators. Uploaded transcripts are
    excluded (only verified non-answer quotes are kept)."""
    lim = {
        "press_release": 6000,
        "cfo_commentary": 4000,
        "risk_added": 5000,
        "risk_removed": 3000,
        "news": 3000,
        "evasive": 5000,
    }
    out = {k: ctx.docs[k][:n] for k, n in lim.items() if ctx.docs.get(k)}
    if query:
        for i, (role, _cid, text) in enumerate(A.retrieve(ctx, query)):
            if role != "transcript":
                out[f"retrieved_{i}_{role}"] = text[:1500]
    return out


def save_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    svc = _svc(config)
    ctx = _restore_ctx(state, svc)
    removed = 0
    sections: dict[str, list[Section]] = {}
    for agent, secs in state["sections"].items():
        fixed = []
        for raw in secs:
            sec = Section.model_validate(raw)
            md, n = A.enforce_grounding(sec.markdown)
            removed += n
            fixed.append(sec.model_copy(update={"markdown": md}))
        sections[agent] = fixed
    brief = DecisionBrief.model_validate(state["brief"]) if state.get("brief") else None
    if brief:

        def g(x: str) -> str:
            nonlocal removed
            md, n = A.enforce_grounding(x)
            removed += n
            return md

        brief = brief.model_copy(
            update={
                "headline": g(brief.headline),
                "setup": [g(x) for x in brief.setup],
                "what_would_change_view": [g(x) for x in brief.what_would_change_view],
                "questions_for_call": [g(x) for x in brief.questions_for_call],
                "key_risks": [g(x) for x in brief.key_risks],
                "scenarios": [sc.model_copy(update={"narrative": g(sc.narrative)}) for sc in brief.scenarios],
            }
        )
    if removed:
        log.warning("grounding_removed_sentences", count=removed)
    agents_out = []
    for a in state.get("agents") or []:
        ao = AgentOutput.model_validate(a)
        agents_out.append(ao.model_copy(update={"sections": sections.get(ao.agent, ao.sections)}))
    answer = Section.model_validate(state["answer"]) if state.get("answer") else None
    if answer:
        md, n = A.enforce_grounding(answer.markdown)
        removed += n
        answer = answer.model_copy(update={"markdown": md})
        agents_out.insert(
            0,
            AgentOutput(
                agent="answer",
                status=AgentStatus.OK,
                sections=[answer],
                confidence=brief.confidence if brief else A.confidence_for(ctx, state["mode"]),
            ),
        )
    settings = get_settings()
    with session_scope() as sess:
        run = sess.get(Run, state["run_id"])
        assert run is not None
        report_id = state.get("report_id") or new_id("rpt")
        usage = svc.llm.usage
        report = Report(
            id=report_id,
            run_id=run.id,
            ticker=state["ticker"],
            mode=RunMode(state["mode"]),
            query=state.get("query"),  # type: ignore[arg-type]
            as_of=cutoff_dt(date.fromisoformat(state["cutoff"])),
            created_at=utcnow(),
            event=ctx.event or EarningsEvent(ticker=state["ticker"]),
            brief=brief,
            agents=agents_out,
            facts=ctx.store.facts,
            citations=ctx.store.citations,
            critique_history=[CritiqueRound.model_validate(c) for c in state.get("critique") or []],
            review_status="pending",
            evidence=_evidence(ctx, state.get("query")),
            notices=[ADVICE_NOTICE] if state.get("intent") == "advice" else [],
            fixture=run.fixture,
            cost_usd=round(usage.cost_usd, 5),
            schema_version=SCHEMA_VERSION,
        )
        if removed:
            report.critique_history.append(
                CritiqueRound(
                    round=len(report.critique_history) + 1,
                    action="accepted",
                    issues=[
                        CritiqueIssue(
                            kind="uncited_number",
                            severity="medium",
                            detail=f"{removed} sentence(s) removed before publishing because they contained uncited numbers",
                        )
                    ],
                )
            )
        row = sess.get(ReportRow, report_id)
        body = report.model_dump(mode="json")
        if row:
            row.body = body
        else:
            sess.add(
                ReportRow(
                    id=report_id,
                    run_id=run.id,
                    user_id=run.user_id,
                    ticker=report.ticker,
                    mode=state["mode"],
                    review_status="pending",
                    body=body,
                    schema_version=SCHEMA_VERSION,
                )
            )
        run.report_id, run.tokens_in, run.tokens_out, run.cost_usd = (
            report_id,
            usage.tokens_in,
            usage.tokens_out,
            usage.cost_usd,
        )
        run.status = "awaiting_review"
        run.finished_at = utcnow()  # research is done; review time is not run latency
        pred = A.base_rate_prediction(ctx)
        if pred and ctx.event and ctx.event.date and not run.fixture:
            for kind, val in pred.items():
                sess.merge(
                    Prediction(
                        id=f"{report_id}:{kind}",
                        report_id=report_id,
                        user_id=run.user_id,
                        ticker=report.ticker,
                        event_date=ctx.event.date,
                        kind=kind,
                        value=val,
                        baseline=0.5 if kind == "p_up_day1" else None,
                        method="base_rate",
                        source="live",
                        made_at=utcnow(),
                    )
                )
    svc.events.emit(
        "run.completed",
        None,
        report_id=report_id,
        cost_usd=round(svc.llm.usage.cost_usd, 5),
        tokens=svc.llm.usage.tokens_in + svc.llm.usage.tokens_out,
        review_required=True,
        settings_env=settings.ENV,
    )
    svc.events.emit("review.required", "Review", report_id=report_id)
    return {"report_id": report_id}


def review_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    decision = interrupt({"kind": "review", "report_id": state.get("report_id")})
    return {"review": decision if isinstance(decision, dict) else {"decision": str(decision)}}


def finalize_node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    r = state.get("review") or {}
    apply_review(
        state["run_id"],
        state.get("report_id") or "",
        r.get("decision", "approved"),
        r.get("notes"),
        r.get("actor", "system"),
    )
    return {}


def apply_review(run_id: str, report_id: str, decision: str, notes: str | None, actor: str) -> None:
    from app.db import Review

    with session_scope() as s:
        row = s.get(ReportRow, report_id)
        run = s.get(Run, run_id)
        if row is None or run is None:
            return
        row.review_status = decision
        body = dict(row.body)
        body["review_status"] = decision
        row.body = body
        run.status = "completed"
        run.pending = None
        s.add(Review(report_id=report_id, reviewer_id=actor, decision=decision, notes=notes))
        audit(s, actor, f"review.{decision}", "report", report_id, notes=notes or "")


# ---------------------------------------------------------------- graph


def _traced(name: str, fn: Any) -> Any:
    def wrapped(state: RunState, config: RunnableConfig) -> dict[str, Any]:
        with span(
            f"node.{name}", run_id=state.get("run_id"), ticker=state.get("ticker"), mode=state.get("mode")
        ):
            return fn(state, config)  # type: ignore[no-any-return]

    wrapped.__name__ = getattr(fn, "__name__", name)
    return wrapped


@lru_cache
def build_graph() -> Any:
    g = StateGraph(RunState)
    g.add_node("scope", _traced("scope", scope_node))
    g.add_node("gather", _traced("gather", gather_node))
    g.add_node("analyze", _traced("analyze", analyze_node))
    g.add_node("brief", _traced("brief", brief_node))
    g.add_node("critique", _traced("critique", critique_node))
    g.add_node("refine", _traced("refine", refine_node))
    g.add_node("save", _traced("save", save_node))
    g.add_node("review", _traced("review", review_node))
    g.add_node("finalize", _traced("finalize", finalize_node))
    g.add_edge(START, "scope")
    g.add_edge("scope", "gather")
    g.add_edge("gather", "analyze")
    g.add_edge("analyze", "brief")
    g.add_edge("brief", "critique")
    g.add_conditional_edges("critique", after_critique, {"refine": "refine", "save": "save"})
    g.add_edge("refine", "critique")
    g.add_edge("save", "review")
    g.add_edge("review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=_checkpointer())


def _checkpointer() -> Any:
    s = get_settings()
    if s.durable_storage:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg import Connection
            from psycopg.rows import dict_row

            conn = Connection.connect(
                s.database_url.replace("postgresql+psycopg://", "postgresql://"),
                autocommit=True,
                prepare_threshold=0,
                row_factory=dict_row,
            )
            saver = PostgresSaver(conn)
            saver.setup()
            return saver
        except Exception as e:  # pragma: no cover - depends on infra
            log.error("postgres_checkpointer_failed", error=redact(str(e)))
    return InMemorySaver()


# ---------------------------------------------------------------- execution


def create_run(
    user_id: str,
    ticker: str,
    mode: str,
    query: str | None,
    as_of: date | None,
    event_date: date | None,
    fixture: bool,
    inputs: dict[str, Any] | None = None,
) -> Run:
    today = datetime.now(NY).date()
    cutoff = min(as_of or today, today)
    with session_scope() as s:
        if s.get(User, user_id) is None:
            s.add(User(id=user_id, is_anonymous=user_id.startswith("anon:")))
            s.flush()
        run = Run(
            id=new_id("run"),
            user_id=user_id,
            ticker=ticker,
            mode=mode,
            query=query,
            as_of=cutoff_dt(cutoff),
            event_date=event_date,
            status="queued",
            fixture=fixture,
            inputs=inputs,
        )
        s.add(run)
        audit(s, user_id, "run.create", "run", run.id, ticker=ticker, mode=mode, fixture=fixture)
    return run


def execute(run_id: str, resume: Any = None) -> str:
    """Run (or resume) a run to its next stop. Blocking; call from a thread or a Celery worker."""
    token = run_id_var.set(run_id)
    try:
        return _execute(run_id, resume)
    finally:
        run_id_var.reset(token)


def _execute(run_id: str, resume: Any) -> str:
    with session_scope() as s:
        run = s.get(Run, run_id)
        assert run is not None
        fixture, mode = run.fixture, run.mode
        state: RunState = {
            "run_id": run.id,
            "ticker": run.ticker,
            "mode": run.mode,
            "query": run.query,
            "cutoff": run.as_of.astimezone(NY).date().isoformat(),
            "event_hint": run.event_date.isoformat() if run.event_date else None,
            "critique": [],
            "feedback": {},
        }
        if resume is None:
            run.status, run.started_at = "running", utcnow()
    events = EventLog(run_id)
    fail = set(_run_inputs(run_id).get("debug_fail") or [])
    svc = Services(
        providers=get_providers(fixture, fail),
        llm=LLMRouter(enabled=not fixture),
        events=events,
        run_id=run_id,
    )
    config = {"configurable": {"thread_id": run_id, "svc": svc}, "recursion_limit": 40}
    graph = build_graph()
    if resume is None:
        events.emit(
            "run.started",
            None,
            ticker=state["ticker"],
            mode=mode,
            as_of=state["cutoff"],
            fixture=fixture,
            agents=[AGENT_LABEL[a] for a in A.MODE_AGENTS[mode]],
        )
    try:
        inp: Any = Command(resume=resume) if resume is not None else state
        stopped: dict[str, Any] | None = None
        for chunk in graph.stream(inp, config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                stopped = chunk["__interrupt__"][0].value
        if stopped:
            kind = stopped.get("kind")
            with session_scope() as s:
                run = s.get(Run, run_id)
                assert run is not None
                run.pending = stopped
                run.status = "awaiting_input" if kind == "clarification" else "awaiting_review"
            if kind == "clarification":
                events.emit(
                    "clarification.required", None, question=stopped["question"], options=stopped["options"]
                )
            return "awaiting_input" if kind == "clarification" else "awaiting_review"
        with session_scope() as s:
            run = s.get(Run, run_id)
            assert run is not None
            run.finished_at = utcnow()
            if run.status not in ("completed",):
                run.status = "completed"
        return "completed"
    except RunCancelled:
        _finish(run_id, "cancelled", None)
        events.emit("run.cancelled", None, message="Stopped by user")
        return "cancelled"
    except ScopeRefused as e:
        err = {"code": "out_of_scope", "message": str(e), "retryable": False}
        _finish(run_id, "failed", err)
        events.emit("run.failed", None, error=err)
        return "failed"
    except NotFound as e:
        err = {"code": "not_found", "message": str(e), "retryable": False}
        _finish(run_id, "failed", err)
        events.emit("run.failed", None, error=err)
        return "failed"
    except Exception as e:
        log.exception("run_failed", error=redact(repr(e)))
        err = {
            "code": "internal",
            "message": "The run failed unexpectedly. It has been logged.",
            "retryable": True,
        }
        _finish(run_id, "failed", err)
        events.emit("run.failed", None, error=err)
        return "failed"
    finally:
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is not None and svc.llm.usage.calls:
                run.tokens_in, run.tokens_out, run.cost_usd = (
                    svc.llm.usage.tokens_in,
                    svc.llm.usage.tokens_out,
                    max(run.cost_usd, svc.llm.usage.cost_usd),
                )


def _finish(run_id: str, status: str, error: dict[str, Any] | None) -> None:
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run:
            run.status, run.error, run.finished_at, run.pending = status, error, utcnow(), None


def has_checkpoint(run_id: str) -> bool:
    try:
        snap = build_graph().get_state({"configurable": {"thread_id": run_id}})
        return bool(snap and snap.next)
    except Exception:
        return False


def events_after(run_id: str, after: int, limit: int = 500) -> list[RunEvent]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > after)
                .order_by(RunEvent.seq)
                .limit(limit)
            ).all()
        )


def stale_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=30)
