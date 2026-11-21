# Earnings Intelligence Platform

Grounded research on US-listed companies' earnings for analysts and portfolio managers.

- **Every number has a source.** Hover any figure to see where it came from and its as-of time.
- **Data that isn't available is labelled as such**, never estimated silently.
- **Research only, not investment advice.**

See [PRODUCT.md](PRODUCT.md) for users and principles, [DESIGN.md](DESIGN.md) for the design system,
and [plan.md](plan.md) for the upgrade plan, findings and every decision made.

## What it does

| Mode | Use | Output |
|---|---|---|
| Pre-earnings | Before a print | Expectation bar, what's priced in, base rates, scenarios, questions for the call |
| Post-earnings | After a print | The print vs consensus and the company's own guidance, quality of the beat, reaction vs history |
| Full cycle | Both | Everything above |
| Question | Ad hoc | A grounded answer over cited facts and the company's own documents |

Each run streams progress live. Each report includes:
- a one-screen **decision brief** (setup, bull/base/bear with base-rate probabilities, what would change the view, unknowns, data freshness)
- the full sections
- the critique history
- a human review step with an append-only audit trail

### How grounding works
1. **Gatherers.** Deterministic code (no LLM) collects facts from SEC EDGAR (XBRL, 8-K items, Form 4, filings), FMP, Yahoo and Tavily.
   - Each fact carries a citation and an as-of time.
   - A fact store rejects anything dated after the run's cutoff, which enforces "no look-ahead".
2. **Press-release extraction.** Figures are kept only if the quoted sentence exists in the filing and contains the number.
3. **Writers.** LLM writers may only reference facts (`{{fact:key}}`). Code removes any sentence that still contains an uncited number before publishing.
4. **Probabilities.** Scenario probabilities come from code (base rates with shrinkage), never from the model.
5. **Critique.** Deterministic checks (uncited numbers, beat/miss contradictions, advice language) plus an LLM rubric. One refine round; residual issues are shown to the reviewer.

## Evidence

**Eval suite** (`evals/`): 50 cases (generated cases with known outcomes plus hand-written edge cases), scored on three layers.
- Layer 1, contract checks on every run.
- Layer 2, per-case expectations.
- Layer 3, an LLM judge.
- CI runs the 13 fixture cases; the latest live run is in `docs/eval-live-20261120/`.

**Back-test** (`evals/backtest/report.md`): 232 prints across 29 large caps, with no look-ahead.
- **Up/down probabilities did not beat a coin flip** (Brier 0.260 vs 0.250). On a time-split holdout, the stock's own history added nothing over the pooled base rate.
- The UI therefore labels direction probabilities as descriptive, not predictive.
- **Expected move size beat the pooled baseline** (error 3.76 vs 3.93 percentage points).

## Run it

```bash
uv venv -p 3.13 && uv pip install -e ".[dev]"
```

```bash
cd frontend && npm ci && npm run build && cd ..
```

```bash
.venv/bin/uvicorn app.main:app --app-dir backend --port 8000
```

- Open http://localhost:8000.
- Without an LLM key the app runs in **fixture mode**: recorded data for NVDA, AAPL, JPM and WMT, clearly bannered.
- Full stack with Postgres, Redis and the worker: `docker compose up --build`.

Checks:

```bash
.venv/bin/ruff check backend evals && .venv/bin/mypy && .venv/bin/pytest -q
```

```bash
.venv/bin/python -m evals.run --fixture
```

```bash
cd frontend && npm run typecheck && npm run lint && npm run test:unit && npm run test:e2e
```

Live evals against a running server (costs about $5 in LLM usage):

```bash
EVAL_HOOKS=1 .venv/bin/uvicorn app.main:app --app-dir backend --port 8020
```

```bash
.venv/bin/python -m evals.run --base-url http://localhost:8020 --judge
```

## Configuration ("needs key" matrix)

| Setting | Enables | Without it |
|---|---|---|
| `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) | Live research | Fixture mode (demo data) |
| `ANTHROPIC_API_KEY` | Cross-provider fallback and judge | OpenAI only |
| `DATABASE_URL` (Postgres) | Durable reports, LangGraph checkpoints | SQLite on local disk (ephemeral on Heroku) |
| `REDIS_URL` | Worker jobs (alerts, snapshots, scoring) | No background jobs |
| `AUTH_JWKS_URL`, `AUTH_ISSUER`, `CLERK_PUBLISHABLE_KEY` | Sign-in; data scoped by user | Anonymous per-browser cookie |
| `SESSION_SECRET` | Signs the anonymous cookie | Dev default (insecure) |
| `SEC_USER_AGENT` ("App name contact@email") | SEC fair-access compliance | Generic agent; SEC may throttle |
| `FMP_API_KEY` | Consensus and calendar | Yahoo fallback for EPS history |
| `FINNHUB_API_KEY` or FMP premium | Estimate dispersion, high/low, count | Shown as unavailable |
| `FRED_API_KEY` | Macro context | Shown as unavailable |
| `TAVILY_API_KEY` | Dated news headlines | No news |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` | Tracing (any OTLP backend, including Langfuse) | Spans dropped |
| `SENTRY_DSN` | Error reporting | Logs only |
| `ADMIN_SUBS` | Global `/api/v1/metrics` | Per-user metrics only |

Data licensing:
- SEC EDGAR: public domain.
- FMP free tier: display only.
- yfinance: unofficial; Yahoo's terms allow personal use only. Verify before any redistribution.
- Transcripts: no licensed source is configured. Users can paste one; it is cited as their upload.

## Architecture

- **API.** FastAPI under `/api/v1`, serving the built React SPA from the same origin.
- **Orchestration.** LangGraph: `scope → gather → analyze → brief → critique ⇄ refine → save → review (interrupt) → finalize`.
- **Streaming.** SSE with persisted, sequence-numbered events (`Last-Event-ID` resume), a cancel endpoint, and one error contract.
- **Worker.** Celery with a single beat. Schedules run in America/New_York and every job is idempotent. It handles the 8-K watch, consensus snapshots and forecast scoring.
- **Deploy.** Heroku container stack (`heroku.yml`): web and worker from one multi-stage image. Smoke test: `scripts/smoke.py <url>`.

## Ops runbook

| Task | How |
|---|---|
| Deploy | Push to `main` (Heroku builds `heroku.yml`). Check `/api/v1/health` (`version`, `git_sha`, `checks`) |
| Smoke test | `python scripts/smoke.py https://<app>.herokuapp.com [--token JWT] [--live]` |
| Migrations | Run automatically at startup (Alembic, idempotent). Manual: `cd backend && alembic upgrade head` |
| Rollback | `heroku releases:rollback -a <app>`. Schema changes are additive; `alembic downgrade -1` reverses the last one |
| Failed background jobs | Inspect the `dead_letters` table |
| Costs and latency | `GET /api/v1/metrics` |
