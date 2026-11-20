import os
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="ei-test-"), "test.db")
_URL = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_DB}"
os.environ.update(
    {
        "ENV": "test",
        "FIXTURE_MODE": "1",
        "DATABASE_URL": _URL,
        "REDIS_URL": "",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "AUTH_JWKS_URL": "",
        "AUTH_ISSUER": "",
        "ALLOW_ANONYMOUS": "1",
        "SENTRY_DSN": "",
        "LOG_LEVEL": "WARNING",
    }
)

import json  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    from app.db import Base, get_engine
    from app.main import app

    Base.metadata.create_all(get_engine())
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limits() -> None:
    from app.auth import reset_rate_limits

    reset_rate_limits()


def new_client() -> TestClient:
    from app.main import app

    return TestClient(app)


def read_events(client: TestClient, run_id: str, timeout: float = 60, after: int = 0) -> list[dict]:
    """Consume the SSE stream until a terminal event (or clarification) arrives."""
    deadline = time.time() + timeout
    events: list[dict] = []
    while time.time() < deadline:
        with client.stream(
            "GET", f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(after)}
        ) as r:
            assert r.status_code == 200
            for line in r.iter_lines():
                if line.startswith("data:"):
                    ev = json.loads(line[5:].strip())
                    events.append(ev)
                    after = ev["seq"]
        if events and events[-1]["type"] in (
            "run.completed",
            "run.failed",
            "run.cancelled",
            "clarification.required",
            "review.required",
        ):
            return events
        if any(e["type"] == "clarification.required" for e in events):
            return events
        time.sleep(0.2)
    raise AssertionError(f"timed out; got {[e['type'] for e in events]}")
