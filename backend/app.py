import logging
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models.schemas import (
    ResearchRequest,
    AgentResultResponse,
    EarningsReport,
    ApprovalRequest,
    WatchlistItem,
    HealthResponse,
)
from graph.orchestrator import run_research, stream_research
from tasks.workers import (
    task_run_research,
    task_ingest_daily_prices,
    task_ingest_earnings_calendar,
)
from tools.snowflake_client import SnowflakeClient
from tools.pinecone_client import PineconeClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

reports_store: dict = {}
watchlist_store: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Earnings Intelligence Platform starting up")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Earnings Intelligence Platform",
    version="2.0.0",
    description="Multi-agent financial research with LangGraph orchestration",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health():
    checks = {"api": True}
    try:
        sf = SnowflakeClient()
        checks["snowflake"] = sf.health_check()
    except Exception:
        checks["snowflake"] = False
    try:
        pc = PineconeClient()
        checks["pinecone"] = pc.health_check()
    except Exception:
        checks["pinecone"] = False
    all_ok = all(checks.values())
    return HealthResponse(status="healthy" if all_ok else "degraded", checks=checks)


@app.post("/research", response_model=EarningsReport)
async def start_research(request: ResearchRequest):
    if request.async_mode:
        task = task_run_research.delay(
            request.ticker, request.query, request.mode, request.earnings_date,
        )
        report = EarningsReport(
            run_id=task.id,
            ticker=request.ticker,
            query=request.query,
            status="running",
        )
        reports_store[task.id] = report
        return report

    result = await run_research(
        ticker=request.ticker,
        query=request.query,
        mode=request.mode,
        earnings_date=request.earnings_date,
        active_agents=request.active_agents,
    )

    run_id = result.get("run_id", "unknown")

    def _to_response(r):
        if r is None:
            return None
        return AgentResultResponse(
            agent_name=r.agent_name,
            response=r.response,
            confidence=r.confidence,
            sources=r.sources,
            structured_data=r.structured_data,
            execution_time_ms=r.execution_time_ms,
        )

    report = EarningsReport(
        run_id=run_id,
        ticker=request.ticker,
        query=request.query,
        status="review",
        pre_earnings=_to_response(result.get("pre_earnings")),
        live_earnings=_to_response(result.get("live_earnings")),
        transcript_analysis=_to_response(result.get("transcript_analysis")),
        post_earnings=_to_response(result.get("post_earnings")),
        synthesis=_to_response(result.get("synthesis")),
        final_report=result.get("final_report"),
    )
    reports_store[run_id] = report
    return report


@app.get("/research/{run_id}", response_model=EarningsReport)
async def get_research(run_id: str):
    if run_id not in reports_store:
        raise HTTPException(status_code=404, detail="Run not found")
    return reports_store[run_id]


@app.post("/research/{run_id}/approve")
async def approve_research(run_id: str, request: ApprovalRequest):
    if run_id not in reports_store:
        raise HTTPException(status_code=404, detail="Run not found")
    report = reports_store[run_id]
    if request.approved:
        report.status = "approved"
    else:
        report.status = "rejected"
    return {"status": report.status, "run_id": run_id}


@app.websocket("/research/{run_id}/stream")
async def stream_research_ws(websocket: WebSocket, run_id: str):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        ticker = data.get("ticker", "NVDA")
        query = data.get("query", "earnings analysis")
        mode = data.get("mode", "full_cycle")

        async for event in stream_research(ticker, query, mode):
            await websocket.send_json(event)

        await websocket.send_json({"type": "complete", "run_id": event.get("run_id", run_id)})
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for run {run_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close(code=1011, reason=str(e))


@app.get("/watchlist", response_model=List[WatchlistItem])
async def get_watchlist():
    return watchlist_store


@app.post("/watchlist", response_model=WatchlistItem)
async def add_to_watchlist(item: WatchlistItem):
    for existing in watchlist_store:
        if existing.ticker == item.ticker:
            raise HTTPException(status_code=409, detail="Ticker already in watchlist")
    watchlist_store.append(item)
    return item


@app.delete("/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str):
    global watchlist_store
    watchlist_store = [w for w in watchlist_store if w.ticker != ticker.upper()]
    return {"status": "removed", "ticker": ticker.upper()}


@app.get("/reports", response_model=List[EarningsReport])
async def list_reports(ticker: Optional[str] = None, limit: int = 20):
    reports = list(reports_store.values())
    if ticker:
        reports = [r for r in reports if r.ticker == ticker.upper()]
    return sorted(reports, key=lambda r: r.run_id, reverse=True)[:limit]


@app.post("/pipelines/prices")
async def trigger_price_ingest(tickers: Optional[List[str]] = None):
    task = task_ingest_daily_prices.delay(tickers)
    return {"task_id": task.id, "status": "queued"}


@app.post("/pipelines/calendar")
async def trigger_calendar_ingest(tickers: Optional[List[str]] = None):
    task = task_ingest_earnings_calendar.delay(tickers)
    return {"task_id": task.id, "status": "queued"}
