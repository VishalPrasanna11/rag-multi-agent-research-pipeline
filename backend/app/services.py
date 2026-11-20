"""Read-side services used by the API and jobs: next events, company profile, track record."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from cachetools import TTLCache
from sqlalchemy import select

from app.config import get_settings
from app.db import Prediction, session_scope
from app.logging import get_logger
from app.market import NY, base_rates, reaction
from app.providers import ProviderError, get_providers

log = get_logger("services")
_event_cache: TTLCache[str, dict[str, Any] | None] = TTLCache(maxsize=512, ttl=3600)


def _fixture() -> bool:
    return get_settings().fixture_mode


def next_event_for(ticker: str) -> dict[str, Any] | None:
    key = f"{ticker}:{_fixture()}"
    if key in _event_cache:
        return _event_cache[key]
    today = datetime.now(NY).date().isoformat()
    try:
        rows = get_providers(_fixture()).earnings_history(ticker).data
    except (ProviderError, Exception) as e:
        log.info("next_event_unavailable", ticker=ticker, error=str(e)[:120])
        _event_cache[key] = None
        return None
    upcoming = sorted(
        [r for r in rows if r.get("date") and r["date"] >= today and r.get("eps_act") is None],
        key=lambda r: r["date"],
    )
    last = sorted(
        [r for r in rows if r.get("date") and r.get("eps_act") is not None], key=lambda r: r["date"]
    )
    ev = None
    if upcoming:
        u = upcoming[0]
        ev = {
            "date": u["date"],
            "time": u.get("time"),
            "status": "estimated",
            "eps_est": u.get("eps_est"),
            "days_until": (date.fromisoformat(u["date"]) - date.fromisoformat(today)).days,
        }
    if last:
        ev = {**(ev or {}), "last_reported": last[-1]["date"]}
    _event_cache[key] = ev
    return ev


def company_profile(ticker: str, fixture: bool) -> dict[str, Any]:
    prov = get_providers(fixture)
    today = datetime.now(NY).date()
    out: dict[str, Any] = {"ticker": ticker, "fixture": fixture, "as_of": datetime.now(UTC).isoformat()}
    try:
        r = prov.resolve(ticker)
        out["company"] = r.data["name"]
        out["cik"] = r.data["cik"]
    except ProviderError as e:
        out["error"] = {"code": "not_found", "message": str(e)}
        return out
    bars: list[dict[str, Any]] = []
    try:
        p = prov.prices(ticker, today - timedelta(days=730), today)
        bars = sorted(p.data, key=lambda b: b["day"])
        out["prices"] = [
            {
                "time": b["day"],
                "open": round(b["open"], 4),
                "high": round(b["high"], 4),
                "low": round(b["low"], 4),
                "close": round(b["close"], 4),
                "volume": b["volume"],
            }
            for b in bars
        ]
        out["prices_source"] = {"title": p.title, "license": p.license_note, "url": p.url}
    except ProviderError as e:
        out["prices"] = []
        out["prices_unavailable"] = str(e)
    events = []
    try:
        h = prov.earnings_history(ticker)
        rows = sorted([x for x in h.data if x.get("date")], key=lambda x: x["date"])
        for x in rows[-16:]:
            d = date.fromisoformat(x["date"])
            ev: dict[str, Any] = {
                "date": x["date"],
                "time": x.get("time"),
                "eps_est": x.get("eps_est"),
                "eps_act": x.get("eps_act"),
                "rev_est": x.get("rev_est"),
                "rev_act": x.get("rev_act"),
            }
            if x.get("eps_act") is not None and x.get("eps_est"):
                ev["surprise_pct"] = round((x["eps_act"] / x["eps_est"] - 1) * 100, 2)
            if x.get("eps_act") is not None and bars:
                rx = reaction(bars, d, x.get("time") or "amc")
                if rx:
                    ev.update(
                        day1_pct=round(rx.day1_pct, 2),
                        gap_pct=round(rx.gap_pct, 2),
                        day5_pct=round(rx.day5_pct, 2) if rx.day5_pct is not None else None,
                        pattern=rx.pattern,
                    )
            events.append(ev)
        out["events_source"] = {"title": h.title, "license": h.license_note}
    except ProviderError as e:
        out["events_unavailable"] = str(e)
    out["events"] = events
    br = base_rates([e for e in events if e.get("day1_pct") is not None])
    out["base_rates"] = br.__dict__
    out["track_record"] = track_record_summary(ticker)
    return out


def track_record_summary(ticker: str | None) -> dict[str, Any]:
    with session_scope() as s:
        q = select(Prediction).where(Prediction.kind == "p_up_day1", Prediction.outcome.is_not(None))
        if ticker:
            q = q.where(Prediction.ticker == ticker)
        rows = list(s.scalars(q).all())
        pending = s.scalars(
            select(Prediction)
            .where(Prediction.kind == "p_up_day1", Prediction.outcome.is_(None))
            .where(*([Prediction.ticker == ticker] if ticker else []))
        ).all()
    by_source: dict[str, Any] = {}
    for src in ("live", "backtest"):
        rs = [r for r in rows if r.source == src and r.outcome is not None]
        if not rs:
            continue
        outs = [float(r.outcome or 0.0) for r in rs]
        brier = sum((r.value - o) ** 2 for r, o in zip(rs, outs, strict=True)) / len(rs)
        base = sum(((r.baseline or 0.5) - o) ** 2 for r, o in zip(rs, outs, strict=True)) / len(rs)
        hits = sum(1 for r in rs if (r.value >= 0.5) == (r.outcome == 1.0))
        bins = []
        for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
            b = [r for r in rs if lo <= r.value < lo + 0.2 or (lo == 0.8 and r.value == 1.0)]
            if b:
                bins.append(
                    {
                        "bin": f"{int(lo * 100)}-{int(lo * 100 + 20)}%",
                        "n": len(b),
                        "predicted": round(sum(r.value for r in b) / len(b), 3),
                        "observed": round(sum(float(r.outcome or 0.0) for r in b) / len(b), 3),
                    }
                )  # type: ignore[misc]
        by_source[src] = {
            "n": len(rs),
            "hit_rate": round(hits / len(rs), 3),
            "brier": round(brier, 4),
            "brier_baseline": round(base, 4),
            "calibration": bins,
        }
    recent = sorted(rows, key=lambda r: r.event_date, reverse=True)[:20]
    return {
        "ticker": ticker,
        "by_source": by_source,
        "pending": len(pending),
        "recent": [
            {
                "ticker": r.ticker,
                "event_date": r.event_date.isoformat(),
                "p_up": round(r.value, 3),
                "outcome_up": r.outcome == 1.0,
                "source": r.source,
            }
            for r in recent
        ],
        "note": "Probabilities are base-rate estimates. Live results accumulate as forecasts are scored after each print.",
    }


__all__ = ["base_rates", "company_profile", "next_event_for", "track_record_summary"]
