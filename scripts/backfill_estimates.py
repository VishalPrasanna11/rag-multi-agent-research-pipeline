"""Backfill historical earnings estimates and calendar from FMP."""
import sys
sys.path.insert(0, "backend")

from pipelines.earnings_ingest import ingest_earnings_calendar, ingest_analyst_estimates
from pipelines.daily_prices import ingest_daily_prices

TICKERS = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]


def main():
    print(f"Backfilling data for: {', '.join(TICKERS)}")

    print("\n--- Earnings Calendar ---")
    ingest_earnings_calendar(TICKERS)

    print("\n--- Analyst Estimates ---")
    ingest_analyst_estimates(TICKERS)

    print("\n--- Daily Prices (30 days) ---")
    ingest_daily_prices(TICKERS, days=30)

    print("\nBackfill complete.")


if __name__ == "__main__":
    main()
