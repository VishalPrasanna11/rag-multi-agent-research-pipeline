from datetime import date

from fastapi.testclient import TestClient

from app import jobs
from app.db import Notification, Prediction, SeenFiling, WatchlistItem, session_scope
from tests.conftest import read_events


def test_claim_is_idempotent() -> None:
    assert jobs.claim("test:slot:1") is True
    assert jobs.claim("test:slot:1") is False


def test_jobs_noop_in_fixture_mode() -> None:
    assert jobs.snapshot_consensus.apply().get()["status"] == "skipped"
    assert jobs.poll_filings.apply().get()["status"] == "skipped"


def test_score_predictions_uses_session_aware_reaction(client: TestClient) -> None:
    with session_scope() as s:
        s.merge(
            Prediction(
                id="t:p_up_day1",
                ticker="NVDA",
                event_date=date(2026, 11, 17),
                kind="p_up_day1",
                value=0.45,
                baseline=0.5,
                method="base_rate",
                source="live",
            )
        )
    out = jobs.score_predictions.apply().get()
    assert out["status"] == "ok" and out["scored"] >= 1
    with session_scope() as s:
        p = s.get(Prediction, "t:p_up_day1")
        assert (
            p is not None and p.outcome == 0.0
        )  # NVDA fell on day 1 after the 2026-11-17 AMC print (fixture)
    tr = client.get("/api/v1/track-record?ticker=NVDA").json()
    assert tr["by_source"]["live"]["n"] >= 1


def test_process_filing_notifies_watchers_once(client: TestClient) -> None:
    client.post("/api/v1/watchlist", json={"ticker": "JPM"})
    user = client.get("/api/v1/me").json()  # anonymous id is hidden; look up through the watchlist row
    with session_scope() as s:
        uid = s.query(WatchlistItem).filter_by(ticker="JPM").first().user_id
        s.merge(SeenFiling(accession="0000000000-26-000001", ticker="JPM", form="8-K", items="2.02,9.01"))
    for _ in range(2):  # redelivery must not duplicate the notification
        jobs.process_filing.apply(args=("JPM", "0000000000-26-000001", "2026-10-13")).get()
    with session_scope() as s:
        n = s.query(Notification).filter_by(user_id=uid, ticker="JPM").count()
    assert n == 1 and user["anonymous"] is True
    assert any(x["ticker"] == "JPM" for x in client.get("/api/v1/notifications").json())


def test_metrics_scoped_to_caller(client: TestClient) -> None:
    run_id = client.post(
        "/api/v1/runs", json={"ticker": "NVDA", "mode": "pre_only", "as_of": "2026-11-14"}
    ).json()["run_id"]
    read_events(client, run_id)
    m = client.get("/api/v1/metrics").json()
    assert m["scope"] == "mine" and m["by_mode"]["pre_only"]["runs"] >= 1
    assert m["by_mode"]["pre_only"]["latency_p50_s"] is not None
