"""Generate historical eval cases with KNOWN outcomes from live data (run once; output is committed).

Picks post-event cases (big beat / big miss / in-line / beat-but-fell), pre-event look-ahead traps (as of the
day before a known print) and split cases. Ground truth = provider EPS actual vs estimate and the realised day-1
move, which the system must reproduce through its own pipeline.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.db import init_db
from app.logging import configure_logging
from app.market import reaction
from app.providers import LiveProviders

TICKERS = [
    "AAPL",
    "MSFT",
    "AMZN",
    "GOOGL",
    "META",
    "NVDA",
    "JPM",
    "WMT",
    "XOM",
    "JNJ",
    "PG",
    "KO",
    "NFLX",
    "TSLA",
    "AVGO",
    "COST",
    "HD",
    "CRM",
    "ORCL",
    "INTC",
    "NKE",
    "TGT",
    "DIS",
    "BA",
    "PFE",
    "UNH",
    "CMG",
    "AMD",
    "LULU",
]


def main() -> None:
    configure_logging("ERROR")
    init_db()
    p = LiveProviders()
    today = date(2026, 11, 20)
    rows = []
    for t in TICKERS:
        try:
            hist = [
                r for r in p.earnings_history(t).data if r.get("eps_act") is not None and r.get("eps_est")
            ]
            bars = sorted(p.prices(t, today - timedelta(days=760), today).data, key=lambda b: b["day"])
        except Exception as e:
            print("skip", t, e)
            continue
        for r in hist:
            d = date.fromisoformat(r["date"])
            if not (today - timedelta(days=700) <= d <= today - timedelta(days=12)):
                continue
            rx = reaction(bars, d, r.get("time") or "amc")
            if not rx:
                continue
            s = (r["eps_act"] / r["eps_est"] - 1) * 100
            rows.append({"t": t, "d": d, "s": s, "d1": rx.day1_pct})
    rows.sort(key=lambda x: x["d"], reverse=True)

    def pick(pred, n, used=set()):  # noqa: B006 - intentional shared set across picks
        out = []
        for r in rows:
            k = (r["t"], r["d"])
            if k in used or any(o["t"] == r["t"] for o in out) or not pred(r):
                continue
            used.add(k)
            out.append(r)
            if len(out) == n:
                break
        return out

    cases = []

    def post(r, cat, extra):
        as_of = r["d"] + timedelta(days=10)
        cases.append(
            {
                "id": f"{cat}_{r['t'].lower()}_{r['d']:%Y%m%d}",
                "category": cat,
                "request": {
                    "ticker": r["t"],
                    "mode": "post_only",
                    "as_of": as_of.isoformat(),
                    "event_date": r["d"].isoformat(),
                },
                "expect": {"status": "completed", "event_status": "reported", **extra},
                "ground_truth": {"eps_surprise_pct": round(r["s"], 2), "day1_pct": round(r["d1"], 2)},
            }
        )

    for r in pick(lambda r: r["s"] >= 10, 4):
        post(
            r,
            "big_beat",
            {"surprise_sign": "+", "surprise_min_abs": 8, "reaction_sign": "+" if r["d1"] > 0 else "-"},
        )
    for r in pick(lambda r: r["s"] <= -8, 4):
        post(
            r,
            "big_miss",
            {"surprise_sign": "-", "surprise_min_abs": 6, "reaction_sign": "+" if r["d1"] > 0 else "-"},
        )
    for r in pick(lambda r: abs(r["s"]) < 1.0, 3):
        post(r, "in_line", {"facts_present": ["surprise.eps_pct", "reaction.day1_pct"]})
    for r in pick(lambda r: r["s"] > 3 and r["d1"] < -3, 3):
        post(r, "beat_but_fell", {"surprise_sign": "+", "reaction_sign": "-"})
    # pre-event look-ahead traps: as of the day before a known print
    for r in pick(lambda r: True, 6):
        cases.append(
            {
                "id": f"pre_lookahead_{r['t'].lower()}_{r['d']:%Y%m%d}",
                "category": "pre_only_lookahead",
                "request": {
                    "ticker": r["t"],
                    "mode": "pre_only",
                    "as_of": (r["d"] - timedelta(days=1)).isoformat(),
                    "event_date": r["d"].isoformat(),
                },
                "expect": {
                    "status": "completed",
                    "event_status": "estimated",
                    "facts_absent": ["actual.eps", "surprise.eps_pct", "reaction.day1_pct"],
                    "facts_present": ["consensus.eps_mean", "base.p_up_day1"],
                },
                "ground_truth": {"eps_surprise_pct": round(r["s"], 2), "day1_pct": round(r["d1"], 2)},
            }
        )
    # non-GAAP-heavy / quality: post_only 50 days after the print so the 10-Q is in XBRL
    for r in pick(lambda r: r["t"] in ("CRM", "ORCL", "PFE", "AVGO", "INTC", "META"), 3):
        cases.append(
            {
                "id": f"quality_{r['t'].lower()}_{r['d']:%Y%m%d}",
                "category": "non_gaap_quality",
                "request": {
                    "ticker": r["t"],
                    "mode": "post_only",
                    "as_of": (r["d"] + timedelta(days=50)).isoformat(),
                    "event_date": r["d"].isoformat(),
                },
                "expect": {
                    "status": "completed",
                    "facts_present": ["quality.eps_change", "quality.cash_conversion"],
                },
                "ground_truth": {"eps_surprise_pct": round(r["s"], 2)},
            }
        )
    out = Path(__file__).parent / "cases" / "generated.yaml"
    out.write_text(
        "# Generated by evals/make_cases.py from live provider data on 2026-11-20. Ground truth is the\n"
        "# provider's EPS actual vs estimate and the realised day-1 move.\n"
        + yaml.safe_dump(cases, sort_keys=False)
    )
    print(f"wrote {len(cases)} cases to {out}")


if __name__ == "__main__":
    main()
