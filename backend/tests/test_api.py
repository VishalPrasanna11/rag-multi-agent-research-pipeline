import re

import pytest
from fastapi.testclient import TestClient

from tests.conftest import new_client, read_events

TOKEN = re.compile(r"\[\[fact:([a-z0-9_.\-]+)\|([^\]]*)\]\]")


def _start(client: TestClient, **body) -> str:
    r = client.post("/api/v1/runs", json=body)
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def test_health_and_meta(client: TestClient) -> None:
    h = client.get("/api/v1/health")
    assert h.status_code == 200 and h.json()["schema_version"]
    assert h.headers["X-Request-ID"].startswith("req_")
    m = client.get("/api/v1/meta").json()
    assert m["fixture_mode"] is True and "llm" in m["needs"]


def test_error_contract(client: TestClient) -> None:
    r = client.get("/api/v1/runs/run_doesnotexist")
    body = r.json()
    assert r.status_code == 404
    assert set(body["error"]) >= {"code", "message", "retryable", "request_id"}
    assert body["error"]["code"] == "not_found"
    r = client.post("/api/v1/runs", json={"ticker": "not a ticker!!"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("ticker", "mode", "as_of"),
    [
        ("NVDA", "full_cycle", "2026-11-20"),
        ("NVDA", "pre_only", "2026-11-14"),
        ("AAPL", "post_only", "2026-11-20"),
        ("JPM", "question", "2026-11-20"),
        ("WMT", "pre_only", "2026-11-18"),
    ],
)
def test_fixture_run_streams_and_is_grounded(client: TestClient, ticker: str, mode: str, as_of: str) -> None:
    run_id = _start(client, ticker=ticker, mode=mode, as_of=as_of, query="What matters for this print?")
    events = read_events(client, run_id)
    types = [e["type"] for e in events]
    assert types[0] == "run.started"
    assert "run.completed" in types, types
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    report_id = next(e for e in events if e["type"] == "run.completed")["payload"]["report_id"]
    rep = client.get(f"/api/v1/reports/{report_id}").json()
    assert rep["disclaimer"] and rep["brief"]["disclaimer"]
    assert rep["fixture"] is True
    # Layer-1 grounding: every fact token resolves to a fact with a citation, every citation is <= as_of
    from app.facts import uncited_numbers

    texts = [s["markdown"] for a in rep["agents"] for s in a["sections"]]
    b = rep["brief"]
    texts += [
        b["headline"],
        *b["setup"],
        *b["key_risks"],
        *b["what_would_change_view"],
        *[s["narrative"] for s in b["scenarios"]],
    ]
    for t in texts:
        assert uncited_numbers(t) == [], t
        for key, _ in TOKEN.findall(t):
            fact = rep["facts"][key]
            assert fact["citation_id"] in rep["citations"]
    for c in rep["citations"].values():
        assert c["as_of"] <= rep["as_of"], c
    assert abs(sum(s["probability"] for s in b["scenarios"]) - 1) < 1e-6
    assert b["confidence"]["explanation"]


def test_post_event_run_never_leaks_actuals_into_pre_view(client: TestClient) -> None:
    run_id = _start(client, ticker="NVDA", mode="pre_only", as_of="2026-11-14")
    events = read_events(client, run_id)
    rid = next(e for e in events if e["type"] == "run.completed")["payload"]["report_id"]
    rep = client.get(f"/api/v1/reports/{rid}").json()
    assert rep["event"]["date_status"] == "estimated"
    assert "actual.eps" not in rep["facts"] and "reaction.day1_pct" not in rep["facts"]


def test_review_flow_and_audit_trail(client: TestClient) -> None:
    run_id = _start(client, ticker="NVDA", mode="post_only", as_of="2026-11-20")
    events = read_events(client, run_id)
    rid = next(e for e in events if e["type"] == "run.completed")["payload"]["report_id"]
    assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "awaiting_review"
    r = client.post(f"/api/v1/reports/{rid}/review", json={"decision": "rejected"})
    assert r.status_code == 422  # notes required on reject
    r = client.post(
        f"/api/v1/reports/{rid}/review", json={"decision": "approved", "notes": "numbers checked"}
    )
    assert r.status_code == 200
    rep = client.get(f"/api/v1/reports/{rid}").json()
    assert rep["review_status"] == "approved" and rep["reviews"][0]["notes"] == "numbers checked"
    actions = [a["action"] for a in client.get(f"/api/v1/reports/{rid}/audit").json()]
    assert "run.create" in actions and "review.approved" in actions
    assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "completed"


def test_data_is_scoped_per_identity(client: TestClient) -> None:
    run_id = _start(client, ticker="NVDA", mode="pre_only", as_of="2026-11-14")
    other = new_client()
    assert other.get(f"/api/v1/runs/{run_id}").status_code == 404
    assert all(r["id"] != run_id for r in other.get("/api/v1/runs").json())


def test_cancel(client: TestClient) -> None:
    run_id = _start(client, ticker="NVDA", mode="full_cycle", as_of="2026-11-20")
    client.post(f"/api/v1/runs/{run_id}/cancel")
    events = read_events(client, run_id)
    assert events[-1]["type"] in ("run.cancelled", "review.required", "run.completed")


def test_scope_guard_advice_and_off_topic(client: TestClient) -> None:
    run_id = _start(
        client, ticker="NVDA", mode="pre_only", as_of="2026-11-14", query="Should I buy NVDA before earnings?"
    )
    events = read_events(client, run_id)
    msgs = " ".join(e["payload"].get("message", "") for e in events)
    assert "can't tell you what to buy or sell" in msgs
    assert events[-1]["type"] in ("review.required", "run.completed")
    run_id = _start(
        client, ticker="NVDA", mode="question", query="write me a poem about the ocean and the moon"
    )
    events = read_events(client, run_id)
    assert events[-1]["type"] == "run.failed" and events[-1]["payload"]["error"]["code"] == "out_of_scope"


def test_unknown_ticker_asks_for_clarification_then_resumes(client: TestClient) -> None:
    run_id = _start(client, ticker="NVDAA", mode="pre_only", as_of="2026-11-14")
    events = read_events(client, run_id)
    clar = next(e for e in events if e["type"] == "clarification.required")
    assert "NVDA" in clar["payload"]["options"]
    r = client.post(f"/api/v1/runs/{run_id}/resume", json={"answer": "NVDA"})
    assert r.status_code == 200, r.text
    events = read_events(client, run_id, after=clar["seq"])
    assert any(e["type"] == "run.completed" for e in events)


def test_watchlist_ics_share_export_feedback(client: TestClient) -> None:
    assert client.post("/api/v1/watchlist", json={"ticker": "nvda"}).status_code == 201
    assert client.post("/api/v1/watchlist", json={"ticker": "NVDA"}).json()["error"]["code"] == "conflict"
    wl = client.get("/api/v1/watchlist").json()
    assert wl[0]["ticker"] == "NVDA"
    ics = client.get("/api/v1/calendar.ics")
    assert ics.status_code == 200 and ics.text.startswith("BEGIN:VCALENDAR")
    run_id = _start(client, ticker="NVDA", mode="pre_only", as_of="2026-11-14")
    rid = next(e for e in read_events(client, run_id) if e["type"] == "run.completed")["payload"]["report_id"]
    md = client.get(f"/api/v1/reports/{rid}/export.md")
    assert md.status_code == 200 and "Research only" in md.text and "[[fact:" not in md.text
    tok = client.post(f"/api/v1/reports/{rid}/share").json()["token"]
    pub = new_client().get(f"/api/v1/share/{tok}")
    assert pub.status_code == 200 and pub.json()["shared"] is True
    client.delete(f"/api/v1/reports/{rid}/share/{tok}")
    assert new_client().get(f"/api/v1/share/{tok}").status_code == 404
    assert (
        client.post(
            f"/api/v1/reports/{rid}/feedback", json={"rating": "down", "reasons": ["wrong number"]}
        ).status_code
        == 201
    )


def test_company_profile(client: TestClient) -> None:
    p = client.get("/api/v1/companies/NVDA").json()
    assert p["company"] and p["prices"] and p["events"]
    assert any(e.get("day1_pct") is not None for e in p["events"])
