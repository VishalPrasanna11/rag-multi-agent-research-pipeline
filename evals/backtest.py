"""Historical back-test of the forward-looking calls, run through the production gatherer with no look-ahead.

For every (ticker, past print) the brief is built "as of" the session before the event: the FactStore cutoff
rejects any data dated later, and the event's own actuals are hidden (date_status = estimated). We score:

  direction:  P(day-1 up) = base.p_up_shrunk (this stock's history, shrunk toward 50%)
  size:       expected |day-1 move| = base.mean_abs_move

Baselines: a coin flip (0.5) and the pooled cross-sectional up-rate / median |move| of all prints before the
cutoff. A claim ships as "validated" only if it beats the baseline (Brier / MAE). Output: markdown + json,
and the forecasts are loaded into the predictions table as source=backtest for the track-record UI.

  python -m evals.backtest [--quarters 8] [--tickers AAPL,MSFT,...]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import init_db
from app.facts import FactStore
from app.gather import GatherCtx, cutoff_dt, expectations, prices_and_reactions, resolve_event
from app.logging import configure_logging
from app.market import reaction
from app.providers import LiveProviders

OUT = Path(__file__).parent / "backtest"
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


def brier(ps: list[float], ys: list[float]) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / len(ps)


def logloss(ps: list[float], ys: list[float]) -> float:
    eps = 1e-6
    return -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in zip(ps, ys, strict=True)
    ) / len(ps)


def export_for_app(res: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Ship the verdict + forecasts inside the app image (backend/app/data) for briefs and the track record."""
    data = Path(__file__).resolve().parents[1] / "backend" / "app" / "data"
    data.mkdir(exist_ok=True)
    (data / "backtest.json").write_text(json.dumps({"summary": res, "rows": rows}, indent=0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarters", type=int, default=8)
    ap.add_argument("--tickers", default=",".join(TICKERS))
    a = ap.parse_args()
    configure_logging("ERROR")
    init_db()
    prov = LiveProviders()
    today = date(2026, 11, 20)
    rows: list[dict[str, Any]] = []
    universe: list[dict[str, Any]] = []  # every past print with outcome, for the pooled baseline
    for t in a.tickers.split(","):
        try:
            hist = sorted(
                [r for r in prov.earnings_history(t).data if r.get("eps_act") is not None and r.get("date")],
                key=lambda r: r["date"],
            )
            bars = sorted(prov.prices(t, today - timedelta(days=365 * 6), today).data, key=lambda b: b["day"])
        except Exception as e:
            print("skip", t, e)
            continue
        for r in hist:
            rx = reaction(bars, date.fromisoformat(r["date"]), r.get("time") or "amc")
            if rx:
                universe.append({"t": t, "date": r["date"], "post_day": rx.post_day, "d1": rx.day1_pct})
        for r in [x for x in hist if x["date"] <= (today - timedelta(days=5)).isoformat()][-a.quarters :]:
            ev = date.fromisoformat(r["date"])
            tod = r.get("time") or "amc"
            rx = reaction(bars, ev, tod)
            if not rx:
                continue
            cut = (
                rx.pre_day
            )  # the last close before the information (BMO: prior session; AMC: event day close)
            if tod == "amc" and cut == ev:
                cut = ev - timedelta(
                    days=1
                )  # AMC: brief is written the day before; event-day close is unknown then
            ctx = GatherCtx(prov, FactStore(cutoff_dt(cut)), t, "pre_only", cut, event_hint=ev)  # type: ignore[arg-type]
            try:
                resolve_event(ctx)
                expectations(ctx)
                prices_and_reactions(ctx)
            except Exception as e:
                print("fail", t, ev, type(e).__name__, e)
                continue
            st = ctx.store
            if st.facts.get("actual.eps") or st.facts.get("reaction.day1_pct"):
                raise AssertionError(f"look-ahead leak for {t} {ev}")
            p = st.num("base.p_up_shrunk")
            m = st.num("base.mean_abs_move")
            n = int(st.num("base.n_events") or 0)
            if p is None or n < 4:
                continue
            prior = [u for u in universe if u["post_day"] <= cut]
            pooled_up = sum(1 for u in prior if u["d1"] > 0) / len(prior) if prior else 0.5
            pooled_move = median(abs(u["d1"]) for u in prior) if prior else 4.0
            rows.append(
                {
                    "ticker": t,
                    "event_date": r["date"],
                    "as_of": cut.isoformat(),
                    "p_up": round(p, 4),
                    "n_hist": n,
                    "exp_abs_move": round(m or 0, 3),
                    "pooled_up": round(pooled_up, 4),
                    "pooled_move": round(pooled_move, 3),
                    "day1_pct": round(rx.day1_pct, 3),
                    "up": 1.0 if rx.day1_pct > 0 else 0.0,
                }
            )
        print(f"{t}: {sum(1 for x in rows if x['ticker'] == t)} events", flush=True)
    if not rows:
        print("no rows")
        return 1
    ps, ys = [r["p_up"] for r in rows], [r["up"] for r in rows]
    pooled = [r["pooled_up"] for r in rows]
    coin = [0.5] * len(rows)
    res = {
        "n": len(rows),
        "tickers": len({r["ticker"] for r in rows}),
        "period": [min(r["event_date"] for r in rows), max(r["event_date"] for r in rows)],
        "direction": {
            "brier_model": round(brier(ps, ys), 4),
            "brier_pooled": round(brier(pooled, ys), 4),
            "brier_coin": round(brier(coin, ys), 4),
            "logloss_model": round(logloss(ps, ys), 4),
            "logloss_pooled": round(logloss(pooled, ys), 4),
            "hit_rate_model": round(
                sum(1 for p, y in zip(ps, ys, strict=True) if (p >= 0.5) == (y == 1.0)) / len(ys), 3
            ),
            "hit_rate_pooled": round(
                sum(1 for p, y in zip(pooled, ys, strict=True) if (p >= 0.5) == (y == 1.0)) / len(ys), 3
            ),
            "base_rate_up": round(sum(ys) / len(ys), 3),
        },
        "size": {
            "mae_model": round(sum(abs(r["exp_abs_move"] - abs(r["day1_pct"])) for r in rows) / len(rows), 3),
            "mae_pooled": round(sum(abs(r["pooled_move"] - abs(r["day1_pct"])) for r in rows) / len(rows), 3),
        },
    }
    bins = []
    for lo in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        b = [r for r in rows if lo <= r["p_up"] < lo + 0.1]
        if b:
            bins.append(
                {
                    "bin": f"{int(lo * 100)}-{int(lo * 100 + 10)}%",
                    "n": len(b),
                    "predicted": round(sum(r["p_up"] for r in b) / len(b), 3),
                    "observed": round(sum(r["up"] for r in b) / len(b), 3),
                }
            )
    res["calibration"] = bins
    d = res["direction"]
    res["verdict"] = {
        "direction_validated": d["brier_model"] < d["brier_pooled"] and d["brier_model"] < d["brier_coin"],
        "size_validated": res["size"]["mae_model"] < res["size"]["mae_pooled"],
    }
    worst = sorted(rows, key=lambda r: abs(r["p_up"] - r["up"]), reverse=True)[:8]
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(
        json.dumps({"summary": res, "rows": rows, "generated_at": datetime.now(UTC).isoformat()}, indent=1)
    )
    md = [
        "# Back-test: base-rate earnings calls (no look-ahead)",
        "",
        f"{res['n']} prints across {res['tickers']} large caps, {res['period'][0]} to {res['period'][1]}. Each brief was built as of the "
        "close before the information, through the production gatherer (FactStore cutoff enforced; the event's actuals hidden).",
        "",
        "## Direction: P(stock up on day 1)",
        "",
        "| Model | Brier (lower is better) | Log loss | Hit rate |",
        "|---|---|---|---|",
        f"| This system (stock's own history, shrunk toward 50%) | {d['brier_model']} | {d['logloss_model']} | {d['hit_rate_model']:.0%} |",
        f"| Pooled cross-sectional base rate | {d['brier_pooled']} | {d['logloss_pooled']} | {d['hit_rate_pooled']:.0%} |",
        f"| Coin flip (0.5) | {d['brier_coin']} | 0.6931 | — |",
        "",
        f"Observed up-rate: {d['base_rate_up']:.0%}.",
        "",
        "## Size: expected |day-1 move|",
        "",
        f"MAE this system: {res['size']['mae_model']} pp · MAE pooled median: {res['size']['mae_pooled']} pp.",
        "",
        "## Calibration",
        "",
        "| Predicted bin | n | Mean predicted | Observed up-rate |",
        "|---|---|---|---|",
        *[f"| {b['bin']} | {b['n']} | {b['predicted']:.0%} | {b['observed']:.0%} |" for b in bins],
        "",
        "## Verdict",
        "",
        f"- Direction call: **{'validated' if res['verdict']['direction_validated'] else 'NOT validated'}** "
        "(must beat both the pooled base rate and a coin flip on Brier).",
        f"- Move-size call: **{'validated' if res['verdict']['size_validated'] else 'NOT validated'}** (must beat the pooled median).",
        "",
        "The UI labels a claim 'unvalidated' unless the verdict above is 'validated'.",
        "",
        "## Where it fails (largest errors)",
        "",
        "| Ticker | Event | P(up) | Day 1 |",
        "|---|---|---|---|",
        *[f"| {w['ticker']} | {w['event_date']} | {w['p_up']:.0%} | {w['day1_pct']:+.1f}% |" for w in worst],
    ]
    (OUT / "report.md").write_text("\n".join(md))
    export_for_app(res, rows)
    print("\n".join(md[:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
