"""Pure, deterministic market math (unit-tested; no I/O).

- XBRL quarterly series with fiscal labels (handles FY != CY and derives Q4 = FY - 9M YTD).
- Session-aware earnings reaction windows (BMO/AMC, America/New_York).
- Straddle-implied move on the first expiry after the event.
- Base rates of reactions by setup.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from statistics import mean, median, pstdev
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


# ---------------------------------------------------------------- XBRL


@dataclass(frozen=True)
class QValue:
    tag: str
    start: date | None
    end: date
    value: float
    fy: int | None
    fp: str | None  # Q1..Q4 / FY
    form: str
    filed: date
    accn: str
    derived: bool = False

    @property
    def label(self) -> str:
        return f"FY{self.fy} {self.fp}" if self.fy and self.fp else self.end.isoformat()


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def quarterly_series(
    company_facts: dict[str, Any], tags: list[str], cutoff: date | None = None
) -> list[QValue]:
    """Discrete-quarter values for the first tag that has data, as known at `cutoff` (filed <= cutoff).

    Duration facts of ~3 months are taken directly; Q4 is derived from the FY value minus the 9-month YTD
    value of the same fiscal year when no discrete Q4 is reported (10-K only reports FY).
    Instant facts (balance sheet) are returned per period end.
    """
    best: list[QValue] = []
    for tag in tags:
        ser = _series_one(company_facts, tag, cutoff)
        if ser and (
            not best or ser[-1].end > best[-1].end or (ser[-1].end == best[-1].end and len(ser) > len(best))
        ):
            best = ser
    return best


def _series_one(company_facts: dict[str, Any], tag: str, cutoff: date | None) -> list[QValue]:
    facts = company_facts.get("facts", {})
    for tag_name in [tag]:
        entry = facts.get(tag_name)
        if not entry:
            continue
        rows = [
            r
            for r in entry["values"]
            if r.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "40-F")
            and (cutoff is None or _d(r.get("filed")) <= cutoff)  # type: ignore[operator]
        ]
        if not rows:
            continue
        instant = "start" not in rows[0]
        by_end: dict[tuple[date | None, date], QValue] = {}
        for r in rows:
            start, end = _d(r.get("start")), _d(r["end"])
            assert end is not None
            q = QValue(
                tag,
                start,
                end,
                float(r["val"]),
                r.get("fy"),
                r.get("fp"),
                r["form"],
                _d(r["filed"]),  # type: ignore[arg-type]
                r["accn"],
            )
            k = (start, end)
            # latest filing wins (restatements), but keep the earliest-filed fiscal label for the period
            if k not in by_end or q.filed >= by_end[k].filed:
                by_end[k] = q
        if instant:
            vals = sorted({v.end: v for v in by_end.values()}.values(), key=lambda v: v.end)
            return _relabel(vals)
        quarters = {v.end: v for v in by_end.values() if v.start and 80 <= (v.end - v.start).days <= 100}
        annual = [v for v in by_end.values() if v.start and 350 <= (v.end - v.start).days <= 380]
        ytd9 = {v.end: v for v in by_end.values() if v.start and 260 <= (v.end - v.start).days <= 285}
        for fy in annual:
            if fy.end in quarters:
                continue
            nine = next((n for n in ytd9.values() if n.start == fy.start), None)
            if nine:
                quarters[fy.end] = QValue(
                    tag,
                    nine.end + timedelta(days=1),
                    fy.end,
                    fy.value - nine.value,
                    fy.fy,
                    "Q4",
                    fy.form,
                    fy.filed,
                    fy.accn,
                    derived=True,
                )
        out = sorted(quarters.values(), key=lambda v: v.end)
        if out:
            return _relabel(out)
    return []


def _relabel(vals: list[QValue]) -> list[QValue]:
    """XBRL `fy`/`fp` on a fact refer to the *filing* (prior-period comparatives inherit the newer filing's
    labels). Re-derive the fiscal label from the period end: a period is labelled by the filing whose own
    report period it is (the latest-ending value in that filing)."""
    own: dict[str, date] = {}
    for v in vals:
        own[v.accn] = max(own.get(v.accn, v.end), v.end)
    labelled = []
    for v in vals:
        if own.get(v.accn) == v.end:
            labelled.append(v)
        else:
            labelled.append(
                QValue(v.tag, v.start, v.end, v.value, None, None, v.form, v.filed, v.accn, v.derived)
            )
    # fill missing labels by stepping quarters from a labelled neighbour
    known = [(i, v) for i, v in enumerate(labelled) if v.fy and v.fp and v.fp.startswith("Q")]
    if not known:
        return labelled
    res = []
    for i, v in enumerate(labelled):
        if v.fy and v.fp and v.fp.startswith("Q"):
            res.append(v)
            continue
        _j, ref = min(known, key=lambda kv: abs(kv[0] - i))
        steps = round((v.end - ref.end).days / 91.3)
        qn = int(ref.fp[1]) - 1 + steps  # type: ignore[index]
        fy = (ref.fy or 0) + qn // 4
        res.append(
            QValue(v.tag, v.start, v.end, v.value, fy, f"Q{qn % 4 + 1}", v.form, v.filed, v.accn, v.derived)
        )
    return res


def split_factor_after(bars: list[dict[str, Any]], filed: date) -> float:
    """Cumulative split ratio for splits effective after `filed` (values filed before a split are pre-split)."""
    f = 1.0
    for b in bars:
        if b.get("split") and date.fromisoformat(b["day"]) > filed:
            f *= float(b["split"])
    return f


def split_adjust(series: list[QValue], bars: list[dict[str, Any]], per_share: bool) -> list[QValue]:
    """Per-share values divide by the later split ratio; share counts multiply."""
    out = []
    for v in series:
        f = split_factor_after(bars, v.filed)
        if f == 1.0:
            out.append(v)
            continue
        val = v.value / f if per_share else v.value * f
        out.append(QValue(v.tag, v.start, v.end, val, v.fy, v.fp, v.form, v.filed, v.accn, v.derived))
    return out


def yoy(series: list[QValue]) -> float | None:
    if len(series) < 5 or series[-5].value == 0:
        return None
    return (series[-1].value / series[-5].value - 1) * 100


# ---------------------------------------------------------------- sessions & reactions


def trading_days(bars: list[dict[str, Any]]) -> list[date]:
    return [date.fromisoformat(b["day"]) for b in bars]


@dataclass(frozen=True)
class Reaction:
    pre_day: date
    post_day: date
    pre_close: float
    post_open: float
    post_close: float
    day1_pct: float
    gap_pct: float
    day5_pct: float | None
    day20_pct: float | None
    pattern: str


def reaction(bars: list[dict[str, Any]], event_day: date, tod: str) -> Reaction | None:
    """Earnings reaction measured from the last close BEFORE the information to the first close AFTER it.

    BMO / during market hours: pre = previous session close, post = event-day session.
    AMC: pre = event-day close, post = next session.
    Unknown: treated as AMC if the event day is a trading day (most US large caps), flagged by caller.
    """
    days = trading_days(bars)
    if not days:
        return None
    idx = {d: i for i, d in enumerate(days)}
    pre_i: int | None
    if tod in ("bmo", "dmh"):
        post_i = next((i for i, d in enumerate(days) if d >= event_day), None)
        if post_i is None or post_i == 0:
            return None
        pre_i = post_i - 1
    else:
        pre_i = idx.get(event_day)
        if pre_i is None:
            earlier = [i for i, d in enumerate(days) if d < event_day]
            pre_i = earlier[-1] if earlier else None
        if pre_i is None or pre_i + 1 >= len(days):
            return None
        post_i = pre_i + 1
    pre, post = bars[pre_i], bars[post_i]
    pc = pre["close"]
    d1 = (post["close"] / pc - 1) * 100
    gap = (post["open"] / pc - 1) * 100
    d5 = (bars[post_i + 4]["close"] / pc - 1) * 100 if post_i + 4 < len(bars) else None
    d20 = (bars[post_i + 19]["close"] / pc - 1) * 100 if post_i + 19 < len(bars) else None
    return Reaction(
        days[pre_i],
        days[post_i],
        pc,
        post["open"],
        post["close"],
        d1,
        gap,
        d5,
        d20,
        classify_pattern(gap, d1, d5),
    )


def classify_pattern(gap: float, d1: float, d5: float | None) -> str:
    if abs(d1) < 2 and abs(gap) < 2:
        return "muted"
    if d5 is None:
        return "gap-up" if d1 > 0 else "gap-down"
    if d1 >= 2:
        if d5 >= d1:
            return "gap-and-go"
        if d5 < d1 * 0.5:
            return "gap-and-fade"
        return "gap-and-hold"
    if d1 <= -2:
        if d5 <= d1:
            return "sell-and-extend"
        if d5 > d1 * 0.5:
            return "dip-and-recover"
        return "sell-and-hold"
    return "mixed"


def realized_vol(bars: list[dict[str, Any]], end: date, window: int = 20) -> float | None:
    closes = [b["close"] for b in bars if date.fromisoformat(b["day"]) <= end][-(window + 1) :]
    if len(closes) < window + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    return pstdev(rets) * math.sqrt(252) * 100


def run_up(bars: list[dict[str, Any]], end: date, days: int = 20) -> float | None:
    closes = [b["close"] for b in bars if date.fromisoformat(b["day"]) <= end]
    if len(closes) <= days:
        return None
    return (closes[-1] / closes[-1 - days] - 1) * 100


# ---------------------------------------------------------------- options


def implied_move(
    spot: float, chains: list[dict[str, Any]], event_day: date | None, today: date
) -> dict[str, Any] | None:
    """ATM straddle / spot on the first expiry on/after the event (and after today)."""
    exps = sorted(chains, key=lambda c: c["expiry"])
    target = None
    for c in exps:
        exp = date.fromisoformat(c["expiry"])
        if exp < today:
            continue
        if event_day is None or exp >= event_day:
            target = c
            break
    if target is None or not target["calls"] or not target["puts"]:
        return None

    def mid(o: dict[str, Any]) -> float:
        b, a = float(o.get("bid") or 0), float(o.get("ask") or 0)
        return (b + a) / 2 if b > 0 and a > 0 else float(o.get("lastPrice") or 0)

    call = min(target["calls"], key=lambda o: abs(o["strike"] - spot))
    put = min(target["puts"], key=lambda o: abs(o["strike"] - spot))
    straddle = mid(call) + mid(put)
    if straddle <= 0:
        return None
    term = []
    for c in exps:
        if not c["calls"]:
            continue
        atm = min(c["calls"], key=lambda o: abs(o["strike"] - spot))
        term.append(
            {"expiry": c["expiry"], "atm_iv_pct": round(float(atm.get("impliedVolatility") or 0) * 100, 1)}
        )
    puts_oi = sum(float(o.get("openInterest") or 0) for c in exps for o in c["puts"])
    calls_oi = sum(float(o.get("openInterest") or 0) for c in exps for o in c["calls"])
    return {
        "expiry": target["expiry"],
        "strike": call["strike"],
        "straddle": straddle,
        "implied_move_pct": straddle / spot * 100,
        "term_structure": term,
        "put_call_oi": (puts_oi / calls_oi) if calls_oi else None,
    }


# ---------------------------------------------------------------- base rates


@dataclass
class BaseRate:
    n: int
    p_up: float | None
    mean_abs_move: float | None
    median_abs_move: float | None
    p_up_given_beat: float | None
    n_beat: int
    p_up_given_miss: float | None
    n_miss: int
    beat_rate: float | None
    mean_drift_5d: float | None


def base_rates(events: list[dict[str, Any]]) -> BaseRate:
    """events: [{day1_pct, day5_pct, surprise_pct}]."""
    d1 = [e["day1_pct"] for e in events if e.get("day1_pct") is not None]
    beats = [
        e
        for e in events
        if e.get("surprise_pct") is not None and e["surprise_pct"] > 0 and e.get("day1_pct") is not None
    ]
    misses = [
        e
        for e in events
        if e.get("surprise_pct") is not None and e["surprise_pct"] < 0 and e.get("day1_pct") is not None
    ]
    with_s = [e for e in events if e.get("surprise_pct") is not None]
    drift = [
        e["day5_pct"] - e["day1_pct"]
        for e in events
        if e.get("day5_pct") is not None and e.get("day1_pct") is not None
    ]
    return BaseRate(
        n=len(d1),
        p_up=(sum(1 for x in d1 if x > 0) / len(d1)) if d1 else None,
        mean_abs_move=mean(abs(x) for x in d1) if d1 else None,
        median_abs_move=median(abs(x) for x in d1) if d1 else None,
        p_up_given_beat=(sum(1 for e in beats if e["day1_pct"] > 0) / len(beats)) if beats else None,
        n_beat=len(beats),
        p_up_given_miss=(sum(1 for e in misses if e["day1_pct"] > 0) / len(misses)) if misses else None,
        n_miss=len(misses),
        beat_rate=(sum(1 for e in with_s if e["surprise_pct"] > 0) / len(with_s)) if with_s else None,
        mean_drift_5d=mean(drift) if drift else None,
    )


def shrink(p: float | None, n: int, prior: float = 0.5, k: int = 8) -> float:
    """Beta-binomial shrinkage toward a prior so small samples don't produce extreme probabilities."""
    if p is None or n == 0:
        return prior
    return (p * n + prior * k) / (n + k)


def close_dt_ny(d: date) -> datetime:
    return datetime.combine(d, time(16, 0), NY).astimezone(UTC)
