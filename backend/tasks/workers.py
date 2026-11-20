import logging
import asyncio
from tasks.celery_app import celery_app
from pipelines.daily_prices import ingest_daily_prices
from pipelines.earnings_ingest import ingest_earnings_calendar, ingest_analyst_estimates
from tools.sec_monitor import SECMonitor

logger = logging.getLogger(__name__)

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]


@celery_app.task(name="tasks.workers.task_ingest_daily_prices")
def task_ingest_daily_prices(tickers=None):
    tickers = tickers or DEFAULT_TICKERS
    logger.info(f"Ingesting daily prices for {tickers}")
    ingest_daily_prices(tickers)
    return {"status": "ok", "tickers": tickers}


@celery_app.task(name="tasks.workers.task_ingest_earnings_calendar")
def task_ingest_earnings_calendar(tickers=None):
    tickers = tickers or DEFAULT_TICKERS
    logger.info(f"Ingesting earnings calendar for {tickers}")
    ingest_earnings_calendar(tickers)
    return {"status": "ok", "tickers": tickers}


@celery_app.task(name="tasks.workers.task_ingest_analyst_estimates")
def task_ingest_analyst_estimates(tickers=None):
    tickers = tickers or DEFAULT_TICKERS
    logger.info(f"Ingesting analyst estimates for {tickers}")
    ingest_analyst_estimates(tickers)
    return {"status": "ok", "tickers": tickers}


@celery_app.task(name="tasks.workers.task_poll_sec_filings")
def task_poll_sec_filings(tickers=None):
    tickers = tickers or DEFAULT_TICKERS
    monitor = SECMonitor()
    for t in tickers:
        monitor.watch(t)
    filings = monitor.poll_once()
    if filings:
        logger.info(f"Found {len(filings)} new filings")
        for filing in filings:
            task_process_filing.delay(filing)
    return {"status": "ok", "filings_found": len(filings)}


@celery_app.task(name="tasks.workers.task_process_filing")
def task_process_filing(filing: dict):
    ticker = filing.get("ticker", "")
    filing_type = filing.get("type", "")
    logger.info(f"Processing {filing_type} filing for {ticker}")

    from pipelines.transcript_ingest import ingest_filing
    text = filing.get("text", "")
    if text:
        ingest_filing(
            ticker=ticker,
            text=text,
            filing_type=filing_type,
            filing_date=filing.get("date", ""),
        )
    return {"status": "ok", "ticker": ticker, "type": filing_type}


@celery_app.task(name="tasks.workers.task_run_research")
def task_run_research(ticker: str, query: str, mode: str = "full_cycle", earnings_date=None):
    from graph.orchestrator import run_research
    logger.info(f"Running async research for {ticker}")
    result = asyncio.run(run_research(ticker, query, mode, earnings_date))
    return {
        "status": "ok",
        "ticker": ticker,
        "run_id": result.get("run_id"),
        "has_report": result.get("final_report") is not None,
    }
