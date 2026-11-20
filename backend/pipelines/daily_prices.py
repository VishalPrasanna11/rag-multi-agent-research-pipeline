import logging
from datetime import datetime, timedelta
from tools.market_data import MarketDataClient
from tools.snowflake_client import SnowflakeClient

logger = logging.getLogger(__name__)


def ingest_daily_prices(tickers: list, days: int = 5):
    market = MarketDataClient()
    snowflake = SnowflakeClient()

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    for ticker in tickers:
        try:
            df = market.get_daily_prices(ticker, start, end)
            if df.empty:
                logger.warning(f"No price data for {ticker}")
                continue

            rows = []
            for _, row in df.iterrows():
                rows.append((
                    ticker,
                    row.name.strftime("%Y-%m-%d") if hasattr(row.name, "strftime") else str(row.name),
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    int(row["Volume"]),
                ))

            if rows:
                snowflake.execute(
                    """
                    INSERT INTO STOCK_PRICES (TICKER, TRADE_DATE, OPEN, HIGH, LOW, CLOSE, VOLUME)
                    VALUES (%(ticker)s, %(trade_date)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s)
                    """,
                    [{"ticker": r[0], "trade_date": r[1], "open": r[2], "high": r[3], "low": r[4], "close": r[5], "volume": r[6]} for r in rows],
                )
                logger.info(f"Ingested {len(rows)} price rows for {ticker}")
        except Exception as e:
            logger.error(f"Failed to ingest prices for {ticker}: {e}")
