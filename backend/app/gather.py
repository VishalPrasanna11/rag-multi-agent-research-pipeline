"""Deterministic fact gatherers. No LLM here: every number is computed from cited source data,
dated by the data itself (filed date, session close, event date), and filtered to <= cutoff.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.facts import FactStore, LookAheadError
from app.logging import get_logger
from app.market import (
    NY,
    QValue,
    base_rates,
    close_dt_ny,
    implied_move,
    quarterly_series,
    reaction,
    realized_vol,
    run_up,
    shrink,
    split_adjust,
)
from app.providers import NotFound, Payload, ProviderError, Providers
from app.schemas import EarningsEvent, SourceKind, TimeOfDay

log = get_logger("gather")

SECTOR_ETF = {
    "Technology": "XLK",
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Materials": "XLB",
}

MATERIAL_8K_ITEMS = {
    "1.01": "Entry into a material agreement",
    "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership",
    "2.01": "Acquisition or disposition of assets",
    "2.05": "Exit or restructuring costs",
    "2.06": "Material impairments",
    "3.01": "Delisting notice",
    "4.01": "Change in auditor",
    "4.02": "Non-reliance on prior financials (restatement)",
    "5.02": "Director/officer departure or appointment",
    "5.03": "Charter/bylaw amendment",
}


@dataclass
class GatherCtx:
    providers: Providers
    store: FactStore
    ticker: str
    mode: str
    cutoff: date
    event_hint: date | None = None
    progress: Callable[[str, str], None] = lambda agent, msg: None
    # filled as we go
    cik: str | None = None
    company: str | None = None
    event: EarningsEvent | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    bars: list[dict[str, Any]] = field(default_factory=list)
    filings: list[dict[str, Any]] = field(default_factory=list)
    company_facts: dict[str, Any] = field(default_factory=dict)
    docs: dict[str, str] = field(default_factory=dict)  # role -> text (press_release, cfo_commentary, ...)
    doc_cites: dict[str, str] = field(default_factory=dict)
    unknowns: list[str] = field(default_factory=list)
    sector: str | None = None

    @property
    def post_event(self) -> bool:
        return self.mode in ("post_only", "full_cycle", "question") and bool(
            self.event and self.event.date_status == "reported"
        )


def _cite(
    ctx: GatherCtx, p: Payload, as_of: datetime, title: str | None = None, locator: str | None = None
) -> str:
    return ctx.store.cite(
        p.source,
        title or p.title,
        min(as_of, _cutoff_dt(ctx)),
        url=p.url,
        locator=locator or p.locator,
        license_note=p.license_note,
    )


def cutoff_dt(d: date) -> datetime:
    """End of the as-of day in New York: the only cutoff definition used by stores and gatherers."""
    return datetime.combine(d, time(23, 59, 59), NY).astimezone(UTC)


def _cutoff_dt(ctx: GatherCtx) -> datetime:
    return cutoff_dt(ctx.cutoff)


def _dt(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), UTC)


# ---------------------------------------------------------------- 1. identity & event


def resolve_identity(ctx: GatherCtx) -> None:
    p = ctx.providers.resolve(ctx.ticker)
    ctx.cik, ctx.company = p.data["cik"], p.data["name"]
    sub = ctx.providers.submissions(ctx.cik)
    ctx.filings = [f for f in sub.data["filings"] if f["filing_date"] <= ctx.cutoff.isoformat()]
    meta = sub.data["meta"]
    cid = _cite(ctx, sub, _cutoff_dt(ctx), title=f"SEC submissions: {meta.get('name')}")
    ctx.store.add("company.name", "Company", meta.get("name") or ctx.company, "", cid)
    ctx.store.add("company.cik", "SEC CIK", ctx.cik, "", cid)
    fye = meta.get("fiscal_year_end")
    if fye:
        ctx.store.add("company.fiscal_year_end", "Fiscal year end (MMDD)", fye, "", cid)
    if meta.get("sic_description"):
        ctx.store.add("company.industry", "SEC industry (SIC)", meta["sic_description"], "", cid)
    former = meta.get("former_names") or []
    if former:
        ctx.store.add(
            "company.former_names", "Former names", "; ".join(f.get("name", "") for f in former[:3]), "", cid
        )


def resolve_event(ctx: GatherCtx) -> None:
    p = ctx.providers.earnings_history(ctx.ticker)
    rows = sorted([r for r in p.data if r.get("date")], key=lambda r: r["date"])
    cut = ctx.cutoff.isoformat()
    ctx.history = [r for r in rows if r["date"] <= cut and r.get("eps_act") is not None]
    upcoming = [r for r in rows if r["date"] > cut]
    reported_on_cutoff = [r for r in rows if r["date"] == cut and r.get("eps_act") is not None]

    target: dict[str, Any] | None = None
    status = "unknown"
    if ctx.event_hint:
        target = next((r for r in rows if r["date"] == ctx.event_hint.isoformat()), None)
    if target is None:
        if ctx.mode == "pre_only":
            target = upcoming[0] if upcoming else None
        else:
            target = (reported_on_cutoff or ctx.history[-1:] or upcoming[:1] or [None])[0]
    if target is None:
        raise NotFound("events", f"no earnings events found for {ctx.ticker}")

    ev_day = date.fromisoformat(target["date"])
    tod = target.get("time") or "unknown"
    # Reported = actual EPS published and the event is on/before the cutoff.
    if target["date"] <= cut and target.get("eps_act") is not None:
        status = "reported"
        if ev_day == ctx.cutoff and tod == "amc":
            status = "reported"
    else:
        status = "estimated"
        target = {**target, "eps_act": None, "rev_act": None}  # never leak actuals into a pre-event view
    # confirmed: an 8-K Item 2.02 filed on the event date (after the fact) or the provider says so
    k8 = next(
        (
            f
            for f in ctx.filings
            if f["form"] == "8-K" and "2.02" in (f["items"] or "") and f["filing_date"] == target["date"]
        ),
        None,
    )
    as_of = min(_cutoff_dt(ctx), close_dt_ny(ev_day) if status == "reported" else _cutoff_dt(ctx))
    cid = _cite(ctx, p, as_of, title=p.title)
    if k8:
        cid = ctx.store.cite(
            SourceKind.SEC_FILING,
            f"8-K Item 2.02 filed {k8['filing_date']}",
            min(_dt(date.fromisoformat(k8["filing_date"])), _cutoff_dt(ctx)),
            url=_filing_url(ctx.cik or "", k8),
            locator=k8["accession"],
        )
        if k8.get("accepted"):
            acc = datetime.fromisoformat(k8["accepted"].replace("Z", "+00:00")).astimezone(NY)
            tod = "bmo" if acc.time() < time(9, 30) else ("amc" if acc.time() >= time(16, 0) else "dmh")
    ctx.event = EarningsEvent(
        ticker=ctx.ticker,
        cik=ctx.cik,
        company=ctx.company,
        date=ev_day,
        time_of_day=TimeOfDay(tod) if tod in TimeOfDay._value2member_map_ else TimeOfDay.UNKNOWN,
        date_status=status,  # type: ignore[arg-type]  # one of the literal values above
        citation_id=cid,
    )
    ctx.store.add("event.date", "Earnings date", ev_day.isoformat(), "", cid)
    ctx.store.add("event.time_of_day", "Release timing", ctx.event.time_of_day.upper(), "", cid)  # type: ignore[union-attr]
    ctx.store.add("event.status", "Date status", status, "", cid)
    if status == "estimated":
        days = (ev_day - ctx.cutoff).days
        ctx.store.add("event.days_until", "Days until earnings", float(days), "days", cid)
    ctx._target = target  # type: ignore[attr-defined]
    ctx._target_payload = p  # type: ignore[attr-defined]


def _filing_url(cik: str, f: dict[str, Any]) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{f['accession'].replace('-', '')}/{f['primary_doc']}"


# ---------------------------------------------------------------- 2. expectation bar


def expectations(ctx: GatherCtx) -> None:
    t = ctx._target  # type: ignore[attr-defined]
    p: Payload = ctx._target_payload  # type: ignore[attr-defined]
    ev = ctx.event
    assert ev and ev.date
    # The provider's estimate is the final pre-event consensus; for a pre-event view as of an earlier date it may
    # include revisions after the cutoff. Label it so (point-in-time consensus needs a paid history).
    as_of = min(_cutoff_dt(ctx), close_dt_ny(ev.date) - timedelta(days=1))
    note = "final pre-event consensus" if ev.date_status == "reported" else "current consensus"
    cid = _cite(ctx, p, as_of, title=f"{p.title} ({note})")
    ctx.store.add("consensus.eps_mean", "Consensus EPS (mean)", t.get("eps_est"), "USD/share", cid)
    ctx.store.add("consensus.revenue_mean", "Consensus revenue (mean)", t.get("rev_est"), "USD", cid)
    for k, lbl in [
        ("eps_median", "Consensus EPS median"),
        ("eps_high", "Consensus EPS high"),
        ("eps_low", "Consensus EPS low"),
        ("n_estimates", "Number of estimates"),
        ("dispersion", "Estimate dispersion"),
    ]:
        ctx.store.unavailable(f"consensus.{k}", lbl, "FINNHUB_API_KEY or FMP premium (analyst-estimates)")
    _revision_trend(ctx)
    # surprise history (last 8 reported quarters)
    hist = [r for r in ctx.history if r.get("eps_est") not in (None, 0)][-8:]
    if hist:
        cid_h = _cite(
            ctx,
            p,
            min(_cutoff_dt(ctx), close_dt_ny(date.fromisoformat(hist[-1]["date"]))),
            title=f"{p.title} (history)",
        )
        beats = sum(1 for r in hist if r["eps_act"] > r["eps_est"])
        ctx.store.add(
            "hist.eps_beat_rate",
            "EPS beat rate (last quarters)",
            beats / len(hist),
            "prob",
            cid_h,
            period=f"last {len(hist)} quarters",
        )
        ctx.store.add("hist.n_quarters", "Quarters in history", float(len(hist)), "count", cid_h)
        avg = sum((r["eps_act"] / r["eps_est"] - 1) * 100 for r in hist) / len(hist)
        ctx.store.add("hist.avg_eps_surprise_pct", "Average EPS surprise", avg, "%", cid_h)


def _revision_trend(ctx: GatherCtx) -> None:
    ev = ctx.event
    assert ev and ev.date
    try:
        from sqlalchemy import select

        from app.db import ConsensusSnapshot, session_scope

        with session_scope() as s:
            rows = s.scalars(
                select(ConsensusSnapshot)
                .where(
                    ConsensusSnapshot.ticker == ctx.ticker,
                    ConsensusSnapshot.event_date == ev.date,
                    ConsensusSnapshot.snapshot_date <= ctx.cutoff,
                )
                .order_by(ConsensusSnapshot.snapshot_date)
            ).all()
            snaps: list[tuple[date, float | None]] = [(x.snapshot_date, x.eps_mean) for x in rows]
    except Exception:
        snaps = []
    if not snaps or snaps[-1][1] is None:
        for d in (30, 60, 90):
            ctx.store.unavailable(
                f"consensus.eps_rev_{d}d",
                f"EPS consensus revision {d}d",
                "daily consensus snapshots (accumulating since deploy) or a paid estimate history",
            )
        return
    last_day, last_v = snaps[-1]
    last = float(last_v or 0)
    for d in (30, 60, 90):
        prior = [v for (sd, v) in snaps if sd <= last_day - timedelta(days=d) and v]
        key = f"consensus.eps_rev_{d}d"
        if prior:
            cid = ctx.store.cite(
                SourceKind.DERIVED,
                "Platform consensus snapshots",
                _dt(last_day),
                locator=f"{ctx.ticker}:{ev.date}",
            )
            ctx.store.add(key, f"EPS consensus revision {d}d", (last / prior[-1] - 1) * 100, "%", cid)
        else:
            ctx.store.unavailable(
                key, f"EPS consensus revision {d}d", f"snapshot history shorter than {d} days"
            )


# ---------------------------------------------------------------- 3. prices, priced-in, base rates


def prices_and_reactions(ctx: GatherCtx) -> None:
    start = ctx.cutoff - timedelta(days=365 * 3 + 30)
    p = ctx.providers.prices(ctx.ticker, start, ctx.cutoff + timedelta(days=40))
    bars_all = sorted(p.data, key=lambda b: b["day"])
    ctx.bars = [b for b in bars_all if b["day"] <= ctx.cutoff.isoformat()]
    if not ctx.bars:
        raise NotFound("prices", "no bars before cutoff")
    last = ctx.bars[-1]
    last_day = date.fromisoformat(last["day"])
    cid_px = _cite(ctx, p, close_dt_ny(last_day), title=f"{p.title} close {last['day']}")
    ctx.store.add("px.last_close", f"Last close ({last['day']})", last["close"], "USD/share", cid_px)
    splits = [b for b in ctx.bars if b.get("split")]
    if splits:
        s = splits[-1]
        ctx.store.add(
            "px.last_split",
            "Most recent split (prices and EPS history are split-adjusted)",
            f"{s['split']:g}-for-1 on {s['day']}",
            "",
            cid_px,
        )
    rv = realized_vol(ctx.bars, last_day)
    if rv is not None:
        ctx.store.add("px.realized_vol_20d", "20-day realised volatility (annualised)", rv, "pct", cid_px)
    ev = ctx.event
    if ev and ev.date and ev.date_status == "reported":
        # measure the setup INTO the print (20 sessions to the last pre-event close), not after it
        cut_day = ev.date.isoformat()
        pre = [
            b
            for b in ctx.bars
            if (b["day"] < cut_day if ev.time_of_day in ("bmo", "dmh") else b["day"] <= cut_day)
        ]
        if pre:
            pre_day = date.fromisoformat(pre[-1]["day"])
            ru_pre = run_up(ctx.bars, pre_day, 20)
            if ru_pre is not None:
                cid_pre = _cite(ctx, p, close_dt_ny(pre_day), title=f"{p.title} close {pre_day}")
                ctx.store.add(
                    "px.runup_into_print",
                    f"Price change, 20 sessions to {pre_day} (into the print)",
                    ru_pre,
                    "%",
                    cid_pre,
                )
                _relative_runup(
                    ctx, start, pre_day, ru_pre, suffix="_into_print", base_key="px.runup_into_print"
                )
    ru = run_up(ctx.bars, last_day, 20)
    if ru is not None:
        ctx.store.add("px.runup_20d", f"Price change, 20 sessions to {last_day}", ru, "%", cid_px)
    _relative_runup(ctx, start, last_day, ru)

    # historical reactions (only events whose post-session close is <= cutoff)
    events = []
    for r in ctx.history[-12:]:
        d = date.fromisoformat(r["date"])
        tod = r.get("time") or "amc"
        rx = reaction(bars_all, d, tod)
        if rx is None or rx.post_day > ctx.cutoff:
            continue
        surprise: float | None = (r["eps_act"] / r["eps_est"] - 1) * 100 if r.get("eps_est") else None
        events.append(
            {
                "date": r["date"],
                "day1_pct": rx.day1_pct,
                "day5_pct": rx.day5_pct
                if rx.day5_pct is not None and (rx.post_day + timedelta(days=7)) <= ctx.cutoff
                else None,
                "surprise_pct": surprise,
                "gap_pct": rx.gap_pct,
                "pattern": rx.pattern,
            }
        )
    ctx._reactions = events  # type: ignore[attr-defined]
    if events:
        br = base_rates(events)
        cid_b = ctx.store.cite(
            SourceKind.DERIVED,
            f"Computed from {len(events)} past earnings reactions (prices + EPS history)",
            min(_cutoff_dt(ctx), close_dt_ny(date.fromisoformat(events[-1]["date"])) + timedelta(days=1)),
            locator=",".join(e["date"] for e in events),
        )
        ctx.store.add("base.n_events", "Past earnings events measured", float(br.n), "count", cid_b)
        ctx.store.add(
            "base.p_up_day1", "Share of past prints with a positive day-1 move", br.p_up, "prob", cid_b
        )
        ctx.store.add("base.mean_abs_move", "Average absolute day-1 move", br.mean_abs_move, "pct", cid_b)
        ctx.store.add("base.median_abs_move", "Median absolute day-1 move", br.median_abs_move, "pct", cid_b)
        if br.n_beat:
            ctx.store.add(
                "base.p_up_given_beat",
                f"Up-move rate after EPS beats (n={br.n_beat})",
                br.p_up_given_beat,
                "prob",
                cid_b,
            )
        if br.n_miss:
            ctx.store.add(
                "base.p_up_given_miss",
                f"Up-move rate after EPS misses (n={br.n_miss})",
                br.p_up_given_miss,
                "prob",
                cid_b,
            )
        if br.mean_drift_5d is not None:
            ctx.store.add(
                "base.drift_5d", "Average post-earnings drift, day 1 to day 5", br.mean_drift_5d, "%", cid_b
            )
        ctx.store.add(
            "base.p_up_shrunk",
            "Day-1 up probability (shrunk toward 50%)",
            shrink(br.p_up, br.n),
            "prob",
            cid_b,
            formula="(p*n + 0.5*8)/(n+8)",
        )
    else:
        ctx.store.unavailable("base.p_up_day1", "Day-1 up-move base rate", "price history around past events")

    # this event's reaction (post-event)
    ev = ctx.event
    if ev and ev.date and ev.date_status == "reported":
        rx = reaction(bars_all, ev.date, ev.time_of_day if ev.time_of_day != "unknown" else "amc")
        if rx and rx.post_day <= ctx.cutoff:
            cid_r = _cite(
                ctx,
                p,
                close_dt_ny(rx.post_day),
                title=f"{p.title}: {rx.pre_day} close to {rx.post_day} close",
            )
            ctx.store.add(
                "reaction.pre_close", f"Pre-event close ({rx.pre_day})", rx.pre_close, "USD/share", cid_r
            )
            ctx.store.add("reaction.gap_pct", "Opening gap", rx.gap_pct, "%", cid_r)
            ctx.store.add("reaction.day1_pct", "Day-1 move (close to close)", rx.day1_pct, "%", cid_r)
            day5_known = rx.day5_pct is not None and (rx.post_day + timedelta(days=7)) <= ctx.cutoff
            if day5_known:
                ctx.store.add("reaction.day5_pct", "Day-5 cumulative move", rx.day5_pct, "%", cid_r)
            ctx.store.add(
                "reaction.pattern",
                "Reaction pattern",
                rx.pattern
                if day5_known
                else ("gap-up" if rx.day1_pct > 0 else "gap-down")
                if abs(rx.day1_pct) >= 2
                else "muted",
                "",
                cid_r,
            )
            mam = ctx.store.num("base.mean_abs_move")
            if mam:
                ctx.store.derive(
                    "reaction.vs_hist_move",
                    "Day-1 move vs historical average absolute move",
                    abs(rx.day1_pct) / mam,
                    "x",
                    ["reaction.day1_pct", "base.mean_abs_move"],
                    "|day1| / mean|day1|",
                )


def _relative_runup(
    ctx: GatherCtx,
    start: date,
    last_day: date,
    ru: float | None,
    suffix: str = "",
    base_key: str = "px.runup_20d",
) -> None:
    if ctx.sector is None:
        try:
            si = ctx.providers.short_interest(ctx.ticker)
            ctx.sector = si.data.get("sector") or ""
        except ProviderError:
            ctx.sector = ""
    for bench, key in [
        ("SPY", f"px.runup_vs_spy{suffix}"),
        (SECTOR_ETF.get(ctx.sector or "", ""), f"px.runup_vs_sector{suffix}"),
    ]:
        if not bench or ru is None:
            continue
        try:
            bp = ctx.providers.prices(bench, start, ctx.cutoff)
        except ProviderError:
            ctx.store.unavailable(key, f"20-session run-up vs {bench or 'sector'}", "benchmark prices")
            continue
        bru = run_up(sorted(bp.data, key=lambda b: b["day"]), last_day, 20)
        if bru is None:
            continue
        cid = _cite(ctx, bp, close_dt_ny(last_day), title=f"{bp.title} close {last_day}")
        bkey = f"px.runup_20d_{bench.lower()}{suffix}"
        ctx.store.add(bkey, f"{bench} change, 20 sessions to {last_day}", bru, "%", cid)
        ctx.store.derive(
            key,
            f"Relative move vs {bench}, 20 sessions to {last_day}",
            ru - bru,
            "%",
            [base_key, bkey],
            f"runup - {bench} runup",
        )


def options_implied(ctx: GatherCtx) -> None:
    ev = ctx.event
    today = datetime.now(NY).date()
    if ctx.cutoff < today - timedelta(days=1):
        ctx.store.unavailable(
            "opt.implied_move_pct",
            "Options-implied move",
            "historical options data (only live snapshots are available)",
        )
        return
    if ev and ev.date_status == "reported":
        return
    p = ctx.providers.options_snapshot(ctx.ticker, ev.date if ev else None)
    im = implied_move(p.data["spot"], p.data["chains"], ev.date if ev else None, today)
    if not im:
        ctx.store.unavailable(
            "opt.implied_move_pct", "Options-implied move", "liquid options after the event"
        )
        return
    cid = _cite(ctx, p, p.as_of, title=f"{p.title}, expiry {im['expiry']}")
    ctx.store.add(
        "opt.implied_move_pct",
        f"Implied move (ATM straddle, {im['expiry']} expiry)",
        im["implied_move_pct"],
        "pct",
        cid,
    )
    ctx.store.add("opt.straddle", "ATM straddle price", im["straddle"], "USD/share", cid)
    if im.get("put_call_oi") is not None:
        ctx.store.add("opt.put_call_oi", "Put/call open interest ratio", im["put_call_oi"], "x", cid)
    if im["term_structure"]:
        front = im["term_structure"][0]["atm_iv_pct"]
        back = im["term_structure"][-1]["atm_iv_pct"]
        ctx.store.add("opt.iv_front", "Front-expiry ATM implied vol", front, "pct", cid)
        ctx.store.add("opt.iv_back", "Back-expiry ATM implied vol", back, "pct", cid)
    if ctx.store.num("base.mean_abs_move"):
        ctx.store.derive(
            "opt.implied_vs_hist",
            "Implied move vs historical average move",
            im["implied_move_pct"] / (ctx.store.num("base.mean_abs_move") or 1),
            "x",
            ["opt.implied_move_pct", "base.mean_abs_move"],
            "implied / mean|day1|",
        )


# ---------------------------------------------------------------- 4. reported financials (XBRL)

REV = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
]
COGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold"]


def _ytd_to_quarters(company_facts: dict[str, Any], tag: str, cutoff: date) -> list[QValue]:
    """Cash-flow tags are reported year-to-date in 10-Qs; difference successive YTD values."""
    entry = company_facts.get("facts", {}).get(tag)
    if not entry:
        return []
    rows = [
        r
        for r in entry["values"]
        if r.get("start")
        and r.get("filed", "9999") <= cutoff.isoformat()
        and r.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A")
    ]
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        k = (r["start"], r["end"])
        if k not in by or r["filed"] >= by[k]["filed"]:
            by[k] = r
    starts: dict[str, list[dict[str, Any]]] = {}
    for (s, _e), r in by.items():
        starts.setdefault(s, []).append(r)
    out: dict[str, QValue] = {}
    for _start, rs in starts.items():
        rs.sort(key=lambda r: r["end"])
        prev = None
        for r in rs:
            d0, d1 = date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
            span = (d1 - d0).days
            val = float(r["val"]) - (float(prev["val"]) if prev else 0.0)
            begin = date.fromisoformat(prev["end"]) + timedelta(days=1) if prev else d0
            if 80 <= (d1 - begin).days <= 100 and span <= 380:
                out[r["end"]] = QValue(
                    tag,
                    begin,
                    d1,
                    val,
                    r.get("fy"),
                    r.get("fp"),
                    r["form"],
                    date.fromisoformat(r["filed"]),
                    r["accn"],
                    derived=prev is not None,
                )
            prev = r
    return sorted(out.values(), key=lambda v: v.end)


def reported_financials(ctx: GatherCtx) -> None:
    assert ctx.cik
    p = ctx.providers.company_facts(ctx.cik)
    ctx.company_facts = p.data
    cf = p.data
    rev = quarterly_series(cf, REV, ctx.cutoff)
    if not rev:
        ctx.store.unavailable(
            "print.revenue", "Reported revenue (XBRL)", "XBRL filings (IFRS/foreign filer?)"
        )
        return
    q = rev[-1]

    def cite_q(v: QValue, what: str) -> str:
        return ctx.store.cite(
            SourceKind.SEC_XBRL,
            f"{what}: {v.tag} {v.label} ({v.form} filed {v.filed})",
            min(_dt(v.filed), _cutoff_dt(ctx)),
            url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ctx.cik}&type={v.form}",
            locator=f"{v.accn}#{v.tag}:{v.end}",
            license_note=p.license_note,
        )

    def series_for(tags: list[str]) -> dict[date, QValue]:
        ser = quarterly_series(cf, tags, ctx.cutoff)
        if tags == ["EarningsPerShareDiluted"]:
            ser = split_adjust(ser, ctx.bars, per_share=True)
        elif tags == ["WeightedAverageNumberOfDilutedSharesOutstanding"]:
            ser = split_adjust(ser, ctx.bars, per_share=False)
        return {v.end: v for v in ser}

    period = q.label
    ev = ctx.event
    if ev and ev.date and ev.date_status == "reported" and (ev.date - q.end).days > 75:
        lag_cid = ctx.store.cite(
            SourceKind.SEC_XBRL,
            f"Latest XBRL filing covers {period} (filed {q.filed})",
            min(_dt(q.filed), _cutoff_dt(ctx)),
            locator=q.accn,
        )
        ctx.store.add(
            "print.xbrl_lag",
            "XBRL coverage",
            f"Full financials for the {ev.date} print are not yet filed (10-Q pending); XBRL figures are for {period}",
            "",
            lag_cid,
        )
        ctx.unknowns.append(
            f"Detailed financials for the quarter reported on {ev.date} are not yet in XBRL; "
            f"quality-of-print metrics refer to {period}."
        )
    ctx.store.add("print.period", "Latest reported fiscal quarter (XBRL)", period, "", cite_q(q, "Period"))
    ctx.store.add("print.period_end", "Quarter end", q.end.isoformat(), "", cite_q(q, "Period"))
    ctx.store.add("print.revenue", f"Revenue {period}", q.value, "USD", cite_q(q, "Revenue"), period=period)
    prior = next((v for v in rev if abs((q.end - v.end).days - 364) <= 10), None)
    if prior:
        ctx.store.add(
            "print.revenue_py",
            f"Revenue {prior.label}",
            prior.value,
            "USD",
            cite_q(prior, "Revenue"),
            period=prior.label,
        )
        ctx.store.derive(
            "print.revenue_yoy",
            "Revenue growth YoY",
            (q.value / prior.value - 1) * 100,
            "%",
            ["print.revenue", "print.revenue_py"],
            "revenue / revenue_py - 1",
        )
    if len(rev) >= 2:
        pq = rev[-2]
        ctx.store.add(
            "print.revenue_pq", f"Revenue {pq.label}", pq.value, "USD", cite_q(pq, "Revenue"), period=pq.label
        )
        ctx.store.derive(
            "print.revenue_qoq",
            "Revenue growth QoQ",
            (q.value / pq.value - 1) * 100,
            "%",
            ["print.revenue", "print.revenue_pq"],
            "revenue / revenue_prev_q - 1",
        )

    def add_line(
        key: str, label: str, tags: list[str], unit: str = "USD"
    ) -> tuple[QValue | None, QValue | None]:
        s = series_for(tags)
        cur, py = s.get(q.end), (s.get(prior.end) if prior else None)
        if cur:
            ctx.store.add(key, f"{label} {period}", cur.value, unit, cite_q(cur, label), period=period)
        if py:
            ctx.store.add(
                key + "_py", f"{label} {py.label}", py.value, unit, cite_q(py, label), period=py.label
            )
        return cur, py

    gp, _gp_py = add_line("print.gross_profit", "Gross profit", ["GrossProfit"])
    if gp is None:
        cogs, _cogs_py = add_line("print.cogs", "Cost of revenue", COGS)
        if cogs:
            ctx.store.derive(
                "print.gross_profit",
                f"Gross profit {period}",
                q.value - cogs.value,
                "USD",
                ["print.revenue", "print.cogs"],
                "revenue - cost of revenue",
            )
    _oi, _ = add_line("print.operating_income", "Operating income", ["OperatingIncomeLoss"])
    _pti, _ = add_line(
        "print.pretax_income",
        "Pre-tax income",
        ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
    )
    _tax, _ = add_line("print.tax", "Income tax", ["IncomeTaxExpenseBenefit"])
    _ni, _ = add_line("print.net_income", "Net income", ["NetIncomeLoss"])
    _eps, _ = add_line("print.eps_diluted_gaap", "GAAP diluted EPS", ["EarningsPerShareDiluted"], "USD/share")
    _sh, _ = add_line(
        "print.diluted_shares", "Diluted shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"], "count"
    )

    for key, num, den, lbl in [
        ("print.gross_margin", "print.gross_profit", "print.revenue", "Gross margin"),
        ("print.gross_margin_py", "print.gross_profit_py", "print.revenue_py", "Gross margin (prior year)"),
        ("print.operating_margin", "print.operating_income", "print.revenue", "Operating margin"),
        (
            "print.operating_margin_py",
            "print.operating_income_py",
            "print.revenue_py",
            "Operating margin (prior year)",
        ),
        ("print.tax_rate", "print.tax", "print.pretax_income", "Effective tax rate"),
        ("print.tax_rate_py", "print.tax_py", "print.pretax_income_py", "Effective tax rate (prior year)"),
    ]:
        a, b = ctx.store.num(num), ctx.store.num(den)
        if a is not None and b:
            ctx.store.derive(key, lbl, a / b * 100, "pct", [num, den], f"{num} / {den}")
    _eps_bridge(ctx)
    _quality(ctx, cf, q, prior, cite_q)


def _eps_bridge(ctx: GatherCtx) -> None:
    """YoY GAAP EPS change decomposed into revenue, operating margin, below-the-line, tax and share count.

    EPS = Rev * OM * (PTI/OI) * (1 - tax) / shares. Contributions are computed sequentially (log-free,
    order: revenue -> margin -> non-operating -> tax -> shares) and sum exactly to the total change.
    """
    need = [
        "print.revenue",
        "print.operating_income",
        "print.pretax_income",
        "print.tax",
        "print.diluted_shares",
    ]
    cur_o = [ctx.store.num(k) for k in need]
    py_o = [ctx.store.num(k + "_py") for k in need]
    if any(v is None or v == 0 for v in [*cur_o, *py_o]):
        ctx.store.unavailable(
            "quality.eps_bridge", "EPS change decomposition", "full income statement in XBRL"
        )
        return
    cur = [float(v or 0) for v in cur_o]
    py = [float(v or 0) for v in py_o]
    r0, oi0, pti0, tax0, s0 = py
    r1, oi1, pti1, tax1, s1 = cur

    def eps(r: float, om: float, nonop: float, keep: float, s: float) -> float:
        return r * om * nonop * keep / s

    f0 = [r0, oi0 / r0, pti0 / oi0, 1 - tax0 / pti0, s0]
    f1 = [r1, oi1 / r1, pti1 / oi1, 1 - tax1 / pti1, s1]
    names = ["revenue", "operating_margin", "non_operating", "tax_rate", "share_count"]
    labels = ["Revenue growth", "Operating margin", "Non-operating items", "Tax rate", "Share count"]
    vals = list(f0)
    base = eps(*vals)
    contrib = {}
    for i, n in enumerate(names):
        before = eps(*vals)
        vals[i] = f1[i]
        contrib[n] = eps(*vals) - before
    total = eps(*vals) - base
    inputs = [*need, *[k + "_py" for k in need]]
    ctx.store.derive(
        "quality.eps_change",
        "Modelled GAAP EPS change YoY (from XBRL lines)",
        total,
        "USD/share",
        inputs,
        "EPS = Rev*OM*(PTI/OI)*(1-tax)/shares",
    )
    for n, lbl in zip(names, labels, strict=True):
        ctx.store.derive(
            f"quality.eps_from_{n}",
            f"EPS change from {lbl.lower()}",
            contrib[n],
            "USD/share",
            inputs,
            f"sequential attribution: {n}",
        )
    if total:
        one_offish = abs(contrib["tax_rate"]) + abs(contrib["share_count"]) + abs(contrib["non_operating"])
        ctx.store.derive(
            "quality.low_quality_share",
            "Share of EPS change from tax, buybacks and non-operating items",
            one_offish / (sum(abs(v) for v in contrib.values()) or 1),
            "prob",
            inputs,
            "(|tax|+|shares|+|non-op|) / sum|contributions|",
        )


def _quality(
    ctx: GatherCtx, cf: dict[str, Any], q: QValue, prior: QValue | None, cite_q: Callable[[QValue, str], str]
) -> None:
    cfo = {v.end: v for v in _ytd_to_quarters(cf, "NetCashProvidedByUsedInOperatingActivities", ctx.cutoff)}
    capex = {v.end: v for v in _ytd_to_quarters(cf, "PaymentsToAcquirePropertyPlantAndEquipment", ctx.cutoff)}
    if q.end in cfo:
        ctx.store.add(
            "quality.cfo",
            f"Operating cash flow {q.label}",
            cfo[q.end].value,
            "USD",
            cite_q(cfo[q.end], "Operating cash flow"),
            period=q.label,
        )
        if q.end in capex:
            ctx.store.add(
                "quality.capex",
                f"Capex {q.label}",
                capex[q.end].value,
                "USD",
                cite_q(capex[q.end], "Capex"),
                period=q.label,
            )
            ctx.store.derive(
                "quality.fcf",
                f"Free cash flow {q.label}",
                cfo[q.end].value - capex[q.end].value,
                "USD",
                ["quality.cfo", "quality.capex"],
                "CFO - capex",
            )
        ni = ctx.store.num("print.net_income")
        if ni:
            ctx.store.derive(
                "quality.cash_conversion",
                "Cash conversion (CFO / net income)",
                cfo[q.end].value / ni,
                "x",
                ["quality.cfo", "print.net_income"],
                "CFO / net income",
            )
            rev = ctx.store.num("print.revenue") or 1
            ctx.store.derive(
                "quality.accruals_pct_rev",
                "Accruals (NI - CFO) as % of revenue",
                (ni - cfo[q.end].value) / rev * 100,
                "pct",
                ["print.net_income", "quality.cfo", "print.revenue"],
                "(NI - CFO) / revenue",
            )
    for tag, key, lbl, den_key, mult in [
        ("AccountsReceivableNetCurrent", "quality.dso", "Days sales outstanding", "print.revenue", 91.25),
        ("InventoryNet", "quality.inventory_days", "Inventory days", "print.cogs_for_days", 91.25),
    ]:
        inst = {v.end: v for v in quarterly_series(cf, [tag], ctx.cutoff)}
        cur = inst.get(q.end)
        if not cur:
            continue
        raw_key = key + "_balance"
        ctx.store.add(raw_key, f"{lbl.split(' ')[0]} balance {q.label}", cur.value, "USD", cite_q(cur, lbl))
        if den_key == "print.cogs_for_days":
            rev_n, gp_n = ctx.store.num("print.revenue"), ctx.store.num("print.gross_profit")
            if rev_n is None or gp_n is None:
                continue
            ctx.store.derive(
                "print.cogs_for_days",
                "Cost of revenue (revenue - gross profit)",
                rev_n - gp_n,
                "USD",
                ["print.revenue", "print.gross_profit"],
                "revenue - gross profit",
            )
        den = ctx.store.num(den_key)
        if den:
            ctx.store.derive(
                key, lbl, cur.value / den * mult, "days", [raw_key, den_key], f"balance / {den_key} * 91.25"
            )
        py = inst.get(prior.end) if prior else None
        if py:
            ctx.store.add(
                raw_key + "_py", f"{lbl.split(' ')[0]} balance {py.label}", py.value, "USD", cite_q(py, lbl)
            )
    dr = {
        v.end: v
        for v in quarterly_series(
            cf, ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"], ctx.cutoff
        )
    }
    if q.end in dr:
        ctx.store.add(
            "quality.deferred_revenue",
            f"Deferred revenue (current) {q.label}",
            dr[q.end].value,
            "USD",
            cite_q(dr[q.end], "Deferred revenue"),
        )
        if prior and prior.end in dr and dr[prior.end].value:
            ctx.store.add(
                "quality.deferred_revenue_py",
                "Deferred revenue (current), prior year",
                dr[prior.end].value,
                "USD",
                cite_q(dr[prior.end], "Deferred revenue"),
            )
            ctx.store.derive(
                "quality.deferred_revenue_yoy",
                "Deferred revenue growth YoY",
                (dr[q.end].value / dr[prior.end].value - 1) * 100,
                "%",
                ["quality.deferred_revenue", "quality.deferred_revenue_py"],
                "dr / dr_py - 1",
            )
    buyback = {v.end: v for v in _ytd_to_quarters(cf, "PaymentsForRepurchaseOfCommonStock", ctx.cutoff)}
    if q.end in buyback:
        ctx.store.add(
            "quality.buybacks",
            f"Share repurchases {q.label}",
            buyback[q.end].value,
            "USD",
            cite_q(buyback[q.end], "Buybacks"),
        )
    # margin trend (last 4 quarters)
    rev_s = {v.end: v for v in quarterly_series(cf, REV, ctx.cutoff)}
    gp_s = {v.end: v for v in quarterly_series(cf, ["GrossProfit"], ctx.cutoff)}
    ends = sorted(set(rev_s) & set(gp_s))[-4:]
    if len(ends) >= 3:
        first, last = ends[0], ends[-1]
        k0, k1 = "quality.gm_trend_start", "quality.gm_trend_end"
        ctx.store.add(
            k0,
            f"Gross margin {rev_s[first].label}",
            gp_s[first].value / rev_s[first].value * 100,
            "pct",
            cite_q(gp_s[first], "Gross margin"),
        )
        ctx.store.add(
            k1,
            f"Gross margin {rev_s[last].label}",
            gp_s[last].value / rev_s[last].value * 100,
            "pct",
            cite_q(gp_s[last], "Gross margin"),
        )
        ctx.store.derive(
            "quality.gm_trend_pp",
            f"Gross margin change over {len(ends) - 1} quarters (pp)",
            gp_s[last].value / rev_s[last].value * 100 - gp_s[first].value / rev_s[first].value * 100,
            "%",
            [k0, k1],
            "GM_last - GM_first",
        )


# ---------------------------------------------------------------- 5. the print vs consensus (post-event)


def print_vs_consensus(ctx: GatherCtx) -> None:
    ev = ctx.event
    if not ev or ev.date_status != "reported" or ev.date is None:
        return
    t = ctx._target  # type: ignore[attr-defined]
    p: Payload = ctx._target_payload  # type: ignore[attr-defined]
    cid = _cite(ctx, p, close_dt_ny(ev.date), title=f"{p.title}: reported {ev.date}")
    ctx.store.add(
        "actual.eps", "Reported EPS (provider basis, usually adjusted)", t.get("eps_act"), "USD/share", cid
    )
    ctx.store.add("actual.revenue", "Reported revenue (provider)", t.get("rev_act"), "USD", cid)
    for a, e, k, lbl in [
        ("actual.eps", "consensus.eps_mean", "surprise.eps_pct", "EPS surprise"),
        ("actual.revenue", "consensus.revenue_mean", "surprise.revenue_pct", "Revenue surprise"),
    ]:
        av, evv = ctx.store.num(a), ctx.store.num(e)
        if av is not None and evv:
            ctx.store.derive(k, lbl, (av / evv - 1) * 100, "%", [a, e], "actual / consensus - 1")
    gaap = ctx.store.num("print.eps_diluted_gaap")
    adj = ctx.store.num("actual.eps")
    period_end = ctx.store.get("print.period_end")
    if (
        gaap is not None
        and adj is not None
        and isinstance(period_end, str)
        and 0 <= (ev.date - date.fromisoformat(period_end)).days <= 75
    ):
        ctx.store.derive(
            "quality.nongaap_gap",
            "Adjusted EPS minus GAAP diluted EPS",
            adj - gaap,
            "USD/share",
            ["actual.eps", "print.eps_diluted_gaap"],
            "adjusted - GAAP",
        )
        if gaap:
            ctx.store.derive(
                "quality.nongaap_gap_pct",
                "Adjustments as % of GAAP EPS",
                (adj / gaap - 1) * 100,
                "%",
                ["actual.eps", "print.eps_diluted_gaap"],
                "adjusted / GAAP - 1",
            )


# ---------------------------------------------------------------- 6. filings: press release, 8-K events, risk factors


def filings_and_documents(ctx: GatherCtx) -> None:
    assert ctx.cik
    ev = ctx.event
    # material 8-K events in the last 180 days
    since = (ctx.cutoff - timedelta(days=180)).isoformat()
    material = []
    for f in ctx.filings:
        if f["form"] != "8-K" or f["filing_date"] < since:
            continue
        for item in (f["items"] or "").split(","):
            item = item.strip()
            if item in MATERIAL_8K_ITEMS:
                material.append((f, item))
    for i, (f, item) in enumerate(material[:8]):
        cid = ctx.store.cite(
            SourceKind.SEC_FILING,
            f"8-K Item {item}: {MATERIAL_8K_ITEMS[item]} ({f['filing_date']})",
            min(_dt(date.fromisoformat(f["filing_date"])), _cutoff_dt(ctx)),
            url=_filing_url(ctx.cik, f),
            locator=f["accession"],
        )
        ctx.store.add(
            f"filing.material_8k_{i}",
            f"8-K Item {item} filed {f['filing_date']}",
            MATERIAL_8K_ITEMS[item],
            "",
            cid,
        )
    cid_n = ctx.store.cite(
        SourceKind.SEC_FILING,
        "SEC submissions (8-K items, last 180 days)",
        _cutoff_dt(ctx),
        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ctx.cik}&type=8-K",
    )
    ctx.store.add(
        "filing.material_8k_count", "Material 8-K events, last 180 days", float(len(material)), "count", cid_n
    )
    form4 = [
        f
        for f in ctx.filings
        if f["form"] == "4" and f["filing_date"] >= (ctx.cutoff - timedelta(days=90)).isoformat()
    ]
    cid4 = ctx.store.cite(
        SourceKind.SEC_FILING,
        "SEC Form 4 filings, last 90 days",
        _cutoff_dt(ctx),
        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ctx.cik}&type=4",
    )
    ctx.store.add(
        "pos.form4_count_90d", "Insider Form 4 filings, last 90 days", float(len(form4)), "count", cid4
    )
    _insiders(ctx, form4[:15])

    # earnings press release + CFO commentary (current and prior quarter)
    releases = [f for f in ctx.filings if f["form"] == "8-K" and "2.02" in (f["items"] or "")]
    if ev and ev.date_status != "reported":
        releases = [f for f in releases if f["filing_date"] < ev.date.isoformat()]  # type: ignore[union-attr]
    for role, f in zip(["press_release", "prior_press_release"], releases[:2], strict=False):
        _fetch_exhibits(ctx, f, role)

    # risk factors: latest vs previous periodic report
    def risk_text(f: dict[str, Any]) -> tuple[dict[str, Any], str, str] | None:
        url = _filing_url(ctx.cik or "", f)
        try:
            d = ctx.providers.document_text(url)
        except ProviderError:
            return None
        return (f, extract_section(d.data, "risk factors"), url)

    # Compare like with like: latest 10-Q vs previous 10-Q when the 10-Q carries a substantive risk section,
    # otherwise latest 10-K vs previous 10-K.
    texts: list[tuple[dict[str, Any], str, str]] = []
    qs = [f for f in ctx.filings if f["form"] == "10-Q"][:2]
    if len(qs) == 2:
        pair = [risk_text(f) for f in qs]
        if all(pair) and all(len(t[1]) > 4000 for t in pair if t):
            texts = [t for t in pair if t]
    if not texts:
        ks = [f for f in ctx.filings if f["form"] == "10-K"][:2]
        pair = [risk_text(f) for f in ks]
        texts = [t for t in pair if t]
    if len(texts) == 2 and texts[0][1] and texts[1][1]:
        (fn, new, url_n), (fo, old, _) = texts
        added, removed = paragraph_diff(old, new)
        cid = ctx.store.cite(
            SourceKind.SEC_FILING,
            f"Risk factors: {fn['form']} {fn['filing_date']} vs {fo['form']} {fo['filing_date']}",
            min(_dt(date.fromisoformat(fn["filing_date"])), _cutoff_dt(ctx)),
            url=url_n,
            locator=f"{fn['accession']}#Item1A",
        )
        ctx.store.add(
            "filing.risk_paragraphs_added", "Risk-factor paragraphs added", float(len(added)), "count", cid
        )
        ctx.store.add(
            "filing.risk_paragraphs_removed",
            "Risk-factor paragraphs removed",
            float(len(removed)),
            "count",
            cid,
        )
        ctx.docs["risk_added"] = "\n\n".join(added[:12])[:12000]
        ctx.docs["risk_removed"] = "\n\n".join(removed[:8])[:6000]
        ctx.doc_cites["risk_added"] = cid
        ctx.doc_cites["risk_removed"] = cid
    else:
        ctx.store.unavailable(
            "filing.risk_paragraphs_added",
            "Risk-factor changes",
            "two periodic filings with an Item 1A section",
        )


def _insiders(ctx: GatherCtx, filings: list[dict[str, Any]]) -> None:
    """Open-market purchases (code P) and sales (code S) by insiders, last 90 days."""
    assert ctx.cik
    buys = sells = sells_plan = 0.0
    buyers: set[str] = set()
    sellers: set[str] = set()
    parsed = 0
    for f in filings:
        try:
            p = ctx.providers.form4(ctx.cik, f["accession"], f["primary_doc"])
        except ProviderError:
            continue
        parsed += 1
        d = p.data
        for tx in d.get("transactions", []):
            value = float(tx.get("shares") or 0) * float(tx.get("price") or 0)
            if tx.get("code") == "P":
                buys += value
                buyers.add(d.get("owner") or "?")
            elif tx.get("code") == "S":
                sells += value
                sellers.add(d.get("owner") or "?")
                if d.get("plan_10b5_1"):
                    sells_plan += value
    if not parsed:
        ctx.store.unavailable("pos.insider_net_usd_90d", "Insider net open-market activity", "Form 4 XML")
        return
    cid = ctx.store.cite(
        SourceKind.SEC_FILING,
        f"{parsed} Form 4 filings (last 90 days), open-market transactions",
        _cutoff_dt(ctx),
        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ctx.cik}&type=4",
        locator=",".join(f["accession"] for f in filings[:parsed]),
        license_note="SEC EDGAR (public domain)",
    )
    ctx.store.add("pos.insider_buys_usd_90d", "Insider open-market purchases, 90 days", buys, "USD", cid)
    ctx.store.add("pos.insider_sales_usd_90d", "Insider open-market sales, 90 days", sells, "USD", cid)
    ctx.store.add("pos.insider_buyers_90d", "Insiders buying, 90 days", float(len(buyers)), "count", cid)
    ctx.store.add("pos.insider_sellers_90d", "Insiders selling, 90 days", float(len(sellers)), "count", cid)
    if sells:
        ctx.store.add(
            "pos.insider_sales_planned_share",
            "Share of insider sales under 10b5-1 plans",
            sells_plan / sells,
            "prob",
            cid,
        )


def attach_transcript(ctx: GatherCtx, text: str, label: str | None) -> None:
    cid = ctx.store.cite(
        SourceKind.USER_UPLOAD,
        f"User-uploaded transcript{': ' + label if label else ''}",
        _cutoff_dt(ctx),
        locator="upload",
        license_note="Provided by the user; verify the source.",
    )
    ctx.docs["transcript"] = text[:250000]
    ctx.doc_cites["transcript"] = cid


def _fetch_exhibits(ctx: GatherCtx, f: dict[str, Any], role: str) -> None:
    assert ctx.cik
    try:
        ex = ctx.providers.filing_exhibits(ctx.cik, f["accession"])
    except ProviderError as e:
        ctx.unknowns.append(f"{role}: exhibits unavailable ({e.source})")
        return
    for doc in ex.data:
        typ = (doc.get("type") or "").upper()
        name = doc.get("name", "").lower()
        if not name.endswith((".htm", ".html", ".txt")):
            continue
        sub = None
        if typ.startswith("EX-99.1") or (typ.startswith("EX-99") and "pr" in name):
            sub = role
        elif typ.startswith("EX-99.2") and "commentary" in (doc.get("description", "") + name).lower():
            sub = role.replace("press_release", "cfo_commentary")
        if not sub or sub in ctx.docs:
            continue
        try:
            d = ctx.providers.document_text(doc["url"])
        except ProviderError:
            continue
        ctx.docs[sub] = d.data[:60000]
        ctx.doc_cites[sub] = ctx.store.cite(
            SourceKind.SEC_FILING,
            f"{typ} {doc.get('description') or ''} (8-K {f['filing_date']})".strip(),
            min(_dt(date.fromisoformat(f["filing_date"])), _cutoff_dt(ctx)),
            url=doc["url"],
            locator=f["accession"],
            license_note=ex.license_note,
        )


def extract_section(text: str, heading: str) -> str:
    """Find the body of 'Item 1A. Risk Factors' (skipping the table of contents)."""
    pat = re.compile(rf"item\s*1a\.?\s*[\-–—:]?\s*{heading}", re.IGNORECASE)
    hits = [m.start() for m in pat.finditer(text)]
    if not hits:
        hits = [m.start() for m in re.finditer(heading, text, re.IGNORECASE)]
    best = ""
    for h in hits:
        tail = text[h:]
        end = re.search(r"\n\s*item\s*(?:1b|2|3)\.?\s", tail[200:], re.IGNORECASE)
        body = tail[: (end.start() + 200) if end else 120000]
        if len(body) > len(best):
            best = body
    return best[:150000]


def _paras(text: str) -> list[str]:
    ps = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in ps if len(p) > 120]


def paragraph_diff(old: str, new: str) -> tuple[list[str], list[str]]:
    a, b = _paras(old), _paras(new)
    a_set = {p[:200] for p in a}
    b_set = {p[:200] for p in b}
    added = [
        p
        for p in b
        if p[:200] not in a_set and not difflib.get_close_matches(p[:200], list(a_set), n=1, cutoff=0.85)
    ]
    removed = [
        p
        for p in a
        if p[:200] not in b_set and not difflib.get_close_matches(p[:200], list(b_set), n=1, cutoff=0.85)
    ]
    return added, removed


# ---------------------------------------------------------------- 7. positioning, valuation, context, news


def positioning(ctx: GatherCtx) -> None:
    p = ctx.providers.short_interest(ctx.ticker)
    d = p.data
    if p.as_of > _cutoff_dt(ctx):
        ctx.store.unavailable(
            "pos.short_pct_float", "Short interest % of float", "point-in-time short interest history"
        )
        return
    cid = _cite(ctx, p, p.as_of)
    if d.get("shortPercentOfFloat") is not None:
        ctx.store.add(
            "pos.short_pct_float",
            "Short interest, % of float",
            float(d["shortPercentOfFloat"]) * 100,
            "pct",
            cid,
        )
    if d.get("shortRatio") is not None:
        ctx.store.add("pos.days_to_cover", "Days to cover", float(d["shortRatio"]), "days", cid)
    if d.get("sharesShort") and d.get("sharesShortPriorMonth"):
        ctx.store.add(
            "pos.short_change_pct",
            "Change in shares short vs prior month",
            (d["sharesShort"] / d["sharesShortPriorMonth"] - 1) * 100,
            "%",
            cid,
        )
    if d.get("quoteType") and d["quoteType"] != "EQUITY":
        ctx.unknowns.append(f"Instrument type is {d['quoteType']}, not a common stock")
    ctx.store.unavailable(
        "pos.13f_change", "Institutional holding change (13F)", "13F ingestion (45-day lag; not built)"
    )


def valuation(ctx: GatherCtx) -> None:
    px = ctx.store.num("px.last_close")
    if not px or not ctx.company_facts:
        return
    eps_q = split_adjust(
        quarterly_series(ctx.company_facts, ["EarningsPerShareDiluted"], ctx.cutoff), ctx.bars, per_share=True
    )
    if len(eps_q) >= 4:
        ttm = sum(v.value for v in eps_q[-4:])
        cid = ctx.store.cite(
            SourceKind.SEC_XBRL,
            f"GAAP diluted EPS, 4 quarters to {eps_q[-1].label}",
            min(_dt(eps_q[-1].filed), _cutoff_dt(ctx)),
            locator=",".join(v.accn for v in eps_q[-4:]),
        )
        ctx.store.add("val.eps_ttm_gaap", "GAAP diluted EPS, trailing 4 quarters", ttm, "USD/share", cid)
        if ttm > 0:
            ctx.store.derive(
                "val.pe_ttm",
                "P/E (trailing GAAP)",
                px / ttm,
                "x",
                ["px.last_close", "val.eps_ttm_gaap"],
                "price / TTM GAAP EPS",
            )
        # 3-year range of trailing P/E at each quarter's filing date
        pes = []
        for i in range(3, len(eps_q)):
            t4 = sum(v.value for v in eps_q[i - 3 : i + 1])
            day = eps_q[i].filed.isoformat()
            bar = next((b for b in reversed(ctx.bars) if b["day"] <= day), None)
            if bar and t4 > 0 and eps_q[i].filed >= ctx.cutoff - timedelta(days=3 * 365):
                pes.append(bar["close"] / t4)
        if len(pes) >= 4:
            cid_r = ctx.store.cite(
                SourceKind.DERIVED,
                "Trailing P/E at each 10-Q/10-K filing date, 3 years",
                _cutoff_dt(ctx),
                locator=f"n={len(pes)}",
            )
            ctx.store.add(
                "val.pe_ttm_3y_median", "Trailing P/E, 3-year median", sorted(pes)[len(pes) // 2], "x", cid_r
            )
            ctx.store.add("val.pe_ttm_3y_low", "Trailing P/E, 3-year low", min(pes), "x", cid_r)
            ctx.store.add("val.pe_ttm_3y_high", "Trailing P/E, 3-year high", max(pes), "x", cid_r)
    est = ctx.store.num("consensus.eps_mean")
    if est and est > 0 and ctx.event and ctx.event.date_status != "reported":
        ctx.store.derive(
            "val.pe_fwd_run_rate",
            "Price / (next-quarter consensus EPS x 4)",
            px / (est * 4),
            "x",
            ["px.last_close", "consensus.eps_mean"],
            "price / (4 * next-Q EPS est)",
        )
    ctx.store.unavailable(
        "val.peer_multiples", "Peer forward multiples", "peer list + peer estimates (FMP premium)"
    )


def season_context(ctx: GatherCtx) -> None:
    start = ctx.cutoff - timedelta(days=30)
    p = ctx.providers.earnings_calendar(start, ctx.cutoff)
    rows = [
        r
        for r in p.data
        if r.get("epsActual") is not None
        and r.get("epsEstimated") not in (None, 0)
        and r.get("date", "") <= ctx.cutoff.isoformat()
    ]
    if len(rows) < 10:
        ctx.store.unavailable(
            "ctx.season_beat_rate", "Season EPS beat rate", "earnings calendar with >= 10 reports"
        )
        return
    beats = sum(1 for r in rows if r["epsActual"] > r["epsEstimated"])
    cid = _cite(ctx, p, _cutoff_dt(ctx), title=f"{p.title} ({start} to {ctx.cutoff})")
    ctx.store.add("ctx.season_n", "Companies reported, last 30 days", float(len(rows)), "count", cid)
    ctx.store.add(
        "ctx.season_beat_rate", "EPS beat rate across reporters, last 30 days", beats / len(rows), "prob", cid
    )
    ctx.store.unavailable(
        "ctx.peer_readthrough", "Peer/supplier read-throughs", "peer mapping source (FMP premium/Finnhub)"
    )
    ctx.store.unavailable("ctx.macro", "Macro / FX / commodity exposure", "FRED_API_KEY")


def news(ctx: GatherCtx) -> None:
    p = ctx.providers.news(ctx.ticker, ctx.cutoff - timedelta(days=21), ctx.cutoff)
    items = []
    for n in p.data[:6]:
        pub = n.get("published")
        try:
            as_of = datetime.fromisoformat(str(pub).replace("Z", "+00:00")) if pub else None
        except ValueError:
            as_of = None
        if as_of is None:
            continue  # undated news can't be placed in time -> excluded (look-ahead safety)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        if as_of > _cutoff_dt(ctx):
            continue
        cid = ctx.store.cite(
            SourceKind.TAVILY, n["title"] or "News", as_of, url=n.get("url"), license_note=p.license_note
        )
        items.append({"title": n["title"], "citation": cid, "snippet": n.get("snippet", "")})
    ctx.docs["news"] = "\n".join(f"- {i['title']} [{i['citation']}]: {i['snippet'][:200]}" for i in items)
    if items:
        ctx.doc_cites["news"] = items[0]["citation"]


# ---------------------------------------------------------------- orchestration

GATHERERS: list[tuple[str, str, Callable[[GatherCtx], None], set[str]]] = [
    # (agent label for progress, message, fn, modes)
    (
        "expectations",
        "Pulling consensus and surprise history",
        expectations,
        {"pre_only", "full_cycle", "post_only", "question"},
    ),
    (
        "market",
        "Measuring run-up, volatility and past reactions",
        prices_and_reactions,
        {"pre_only", "full_cycle", "post_only", "question"},
    ),
    ("market", "Reading the options-implied move", options_implied, {"pre_only", "full_cycle", "question"}),
    (
        "fundamentals",
        "Reading XBRL financials from SEC",
        reported_financials,
        {"pre_only", "full_cycle", "post_only", "question"},
    ),
    (
        "fundamentals",
        "Comparing the print with consensus",
        print_vs_consensus,
        {"full_cycle", "post_only", "question"},
    ),
    (
        "filings",
        "Reading the press release, 8-K events and risk factors",
        filings_and_documents,
        {"pre_only", "full_cycle", "post_only", "question"},
    ),
    ("market", "Checking short interest", positioning, {"pre_only", "full_cycle", "post_only"}),
    ("fundamentals", "Computing valuation", valuation, {"pre_only", "full_cycle", "post_only", "question"}),
    ("context", "Scoring this earnings season", season_context, {"pre_only", "full_cycle", "post_only"}),
    ("context", "Collecting dated news", news, {"pre_only", "full_cycle", "post_only", "question"}),
]


def gather_all(ctx: GatherCtx, is_cancelled: Callable[[], bool] = lambda: False) -> None:
    ctx.progress("data", f"Resolving {ctx.ticker} on SEC EDGAR")
    resolve_identity(ctx)
    ctx.progress("data", "Finding the earnings event")
    resolve_event(ctx)
    for agent, msg, fn, modes in GATHERERS:
        if is_cancelled():
            return
        if ctx.mode not in modes:
            continue
        ctx.progress(agent, msg)
        try:
            fn(ctx)
        except LookAheadError as e:
            log.error("lookahead_blocked", gatherer=fn.__name__, error=str(e))
            ctx.unknowns.append(f"{fn.__name__}: data after the as-of date was excluded")
        except ProviderError as e:
            log.warning("gatherer_unavailable", gatherer=fn.__name__, error=str(e))
            ctx.unknowns.append(
                f"{msg}: unavailable ({e.source}{'' if e.retryable else ', not configured or not on plan'})"
            )
        except Exception as e:  # a bug in one gatherer must not sink the run
            log.exception("gatherer_failed", gatherer=fn.__name__, error=str(e))
            ctx.unknowns.append(f"{msg}: failed ({type(e).__name__})")
