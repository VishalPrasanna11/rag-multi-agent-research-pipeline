"""Post-deploy smoke check.

  python scripts/smoke.py https://<app>.herokuapp.com [--token <JWT>] [--live]

Checks: health/version, SPA served, error contract + request id, auth enforcement (when configured),
a fixture research run end to end over SSE (grounded, with a disclaimer), and a live run when --live and the
server has keys. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--token")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    h = {"Authorization": f"Bearer {a.token}"} if a.token else {}
    c = httpx.Client(timeout=60, follow_redirects=True)
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
        if not ok:
            fails.append(name)

    r = c.get(f"{base}/api/v1/health")
    hj = r.json() if r.status_code == 200 else {}
    check(
        "health", r.status_code == 200 and hj.get("checks", {}).get("database") == "ok", json.dumps(hj)[:200]
    )
    check("request_id", r.headers.get("x-request-id", "").startswith("req_"))
    meta = c.get(f"{base}/api/v1/meta").json()
    check("spa", '<div id="root">' in c.get(f"{base}/").text)
    e = c.get(f"{base}/api/v1/runs/run_nope", headers=h)
    check(
        "error_contract",
        e.status_code in (401, 404)
        and {"code", "message", "retryable", "request_id"} <= set(e.json()["error"]),
    )
    if meta["auth"]["configured"]:
        un = c.get(f"{base}/api/v1/runs")
        check(
            "auth_enforced",
            un.status_code == 401 or meta["auth"]["allow_anonymous"],
            f"status={un.status_code}",
        )
        bad = c.get(f"{base}/api/v1/runs", headers={"Authorization": "Bearer not-a-jwt"})
        check("bad_token_rejected", bad.status_code == 401, f"status={bad.status_code}")
    else:
        print("INFO auth not configured on server (anonymous cookie mode); auth enforcement not checked")

    def run(body: dict) -> dict | None:
        rr = c.post(f"{base}/api/v1/runs", json=body, headers=h)
        if rr.status_code != 202:
            check("run_start", False, rr.text[:200])
            return None
        rid, last, t0 = rr.json()["run_id"], 0, time.time()
        while time.time() - t0 < 300:
            with c.stream(
                "GET", f"{base}/api/v1/runs/{rid}/events", headers={**h, "Last-Event-ID": str(last)}
            ) as s:
                if s.status_code >= 400:
                    check("run_stream", False, f"HTTP {s.status_code} (identity cookie not returned?)")
                    return None
                for line in s.iter_lines():
                    if line.startswith("data:"):
                        ev = json.loads(line[5:])
                        last = ev["seq"]
                        if ev["type"] == "run.completed":
                            return c.get(
                                f"{base}/api/v1/reports/{ev['payload']['report_id']}", headers=h
                            ).json()
                        if ev["type"] in ("run.failed", "run.cancelled"):
                            check("run", False, json.dumps(ev["payload"])[:200])
                            return None
            time.sleep(1)
        check("run", False, "timeout")
        return None

    rep = run({"ticker": "NVDA", "mode": "pre_only", "as_of": "2026-11-14", "fixture": True})
    if rep:
        check(
            "fixture_run_grounded",
            bool(rep.get("brief")) and bool(rep.get("disclaimer")) and len(rep["citations"]) > 20,
            f"{len(rep['facts'])} facts",
        )
    if a.live and not meta["fixture_mode"]:
        rep = run({"ticker": "AAPL", "mode": "pre_only"})
        if rep:
            check(
                "live_run", not rep.get("fixture") and bool(rep.get("brief")), f"cost=${rep.get('cost_usd')}"
            )
    elif a.live:
        print("INFO server is in fixture mode (no LLM key): live run skipped")
    print(f"\n{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
