"""Record live provider payloads into backend/app/fixtures/<TICKER>.json for offline tests and fixture mode.

Usage: python -m app.tools.record_fixture NVDA --mode full_cycle --as-of 2026-11-20 [--mode pre_only --as-of ...]
SEC data is public domain. Market-data snapshots are small test samples (not for redistribution).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from typing import Any

from app.db import init_db
from app.facts import FactStore
from app.gather import GatherCtx, cutoff_dt, extract_section, gather_all
from app.logging import configure_logging
from app.providers import FIXTURE_DIR, LiveProviders, Payload


def _rec(p: Payload) -> dict[str, Any]:
    d = asdict(p)
    d["as_of"] = p.as_of.isoformat()
    d["source"] = str(p.source.value if hasattr(p.source, "value") else p.source)
    d.pop("extra", None)
    return d


class Recorder:
    name = "live"

    def __init__(self, live: LiveProviders, out: dict[str, Any]) -> None:
        self.live, self.out = live, out

    def resolve(self, ticker: str) -> Payload:
        p = self.live.resolve(ticker)
        self.out["resolve"] = _rec(p)
        return p

    def submissions(self, cik: str) -> Payload:
        p = self.live.submissions(cik)
        keep = {"8-K", "10-Q", "10-K", "4", "10-Q/A", "10-K/A"}
        rec = _rec(p)
        rec["data"] = {
            "meta": p.data["meta"],
            "filings": [f for f in p.data["filings"] if f["form"] in keep][:400],
        }
        self.out["submissions"] = rec
        return p

    def company_facts(self, cik: str) -> Payload:
        p = self.live.company_facts(cik)
        self.out["company_facts"] = _rec(p)
        return p

    def filing_exhibits(self, cik: str, accession: str) -> Payload:
        p = self.live.filing_exhibits(cik, accession)
        self.out.setdefault("exhibits", {})[accession] = _rec(p)
        return p

    def document_text(self, url: str) -> Payload:
        p = self.live.document_text(url)
        rec = _rec(p)
        text = p.data
        if len(text) > 80000:  # periodic reports: keep only the risk-factor section
            text = extract_section(text, "risk factors")[:90000] or text[:60000]
        rec["data"] = text
        self.out.setdefault("documents", {})[url] = rec
        return Payload(text, p.source, p.title, p.as_of, url=p.url, license_note=p.license_note)

    def form4(self, cik: str, accession: str, primary_doc: str) -> Payload:
        p = self.live.form4(cik, accession, primary_doc)
        self.out.setdefault("form4", {})[accession] = _rec(p)
        return p

    def earnings_history(self, ticker: str) -> Payload:
        p = self.live.earnings_history(ticker)
        self.out["earnings_history"] = _rec(p)
        return p

    def prices(self, ticker: str, start: date, end: date) -> Payload:
        p = self.live.prices(ticker, start, end)
        key = "prices" if ticker == self.out["_ticker"] else f"prices_{ticker}"
        prev = self.out.get(key, {}).get("data", [])
        rec = _rec(p)
        rec["data"] = sorted({b["day"]: b for b in [*prev, *p.data]}.values(), key=lambda b: b["day"])
        self.out[key] = rec
        return p

    def options_snapshot(self, ticker: str, event_date: date | None) -> Payload:
        p = self.live.options_snapshot(ticker, event_date)
        rec = _rec(p)
        for c in rec["data"]["chains"]:  # trim to strikes near the money
            spot = rec["data"]["spot"]
            c["calls"] = [o for o in c["calls"] if abs(o["strike"] / spot - 1) < 0.2]
            c["puts"] = [o for o in c["puts"] if abs(o["strike"] / spot - 1) < 0.2]
        self.out["options"] = rec
        return p

    def short_interest(self, ticker: str) -> Payload:
        p = self.live.short_interest(ticker)
        if ticker == self.out["_ticker"]:
            self.out["short_interest"] = _rec(p)
        return p

    def news(self, ticker: str, start: date, end: date) -> Payload:
        p = self.live.news(ticker, start, end)
        rec = _rec(p)
        rec["data"] = [{**n, "snippet": (n.get("snippet") or "")[:200]} for n in p.data]
        self.out["news"] = rec
        return p

    def earnings_calendar(self, start: date, end: date) -> Payload:
        p = self.live.earnings_calendar(start, end)
        rec = _rec(p)
        rec["data"] = [
            {
                k: r.get(k)
                for k in ("symbol", "date", "epsActual", "epsEstimated", "revenueActual", "revenueEstimated")
            }
            for r in p.data
        ][:400]
        self.out["calendar"] = rec
        return p


def record(ticker: str, runs: list[tuple[str, date]]) -> None:
    path = FIXTURE_DIR / f"{ticker}.json"
    out: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    out["_ticker"] = ticker
    rec = Recorder(LiveProviders(), out)
    for mode, as_of in runs:
        ctx = GatherCtx(rec, FactStore(cutoff_dt(as_of)), ticker, mode, as_of)  # type: ignore[arg-type]
        gather_all(ctx)
        print(f"{ticker} {mode} {as_of}: {len(ctx.store.ok_keys())} facts, unknowns={ctx.unknowns}")
    # benchmark prices live under their own keys so FixtureProviders can serve them
    path.write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    configure_logging("WARNING")
    init_db()
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--mode", action="append", default=[])
    ap.add_argument("--as-of", action="append", default=[])
    a = ap.parse_args()
    modes = a.mode or ["full_cycle"]
    dates = [date.fromisoformat(d) for d in (a.as_of or [date.today().isoformat()])]
    record(a.ticker.upper(), list(zip(modes, dates, strict=True)))
