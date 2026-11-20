import logging
from tools.fmp_client import FMPClient
from tools.snowflake_client import SnowflakeClient

logger = logging.getLogger(__name__)


def ingest_earnings_calendar(tickers: list):
    fmp = FMPClient()
    snowflake = SnowflakeClient()

    for ticker in tickers:
        try:
            calendar = fmp.get_earnings_calendar(ticker)
            if not calendar:
                continue

            for entry in calendar:
                snowflake.execute(
                    """
                    MERGE INTO EARNINGS_CALENDAR AS target
                    USING (SELECT %(ticker)s AS TICKER, %(date)s AS EARNINGS_DATE) AS source
                    ON target.TICKER = source.TICKER AND target.EARNINGS_DATE = source.EARNINGS_DATE
                    WHEN NOT MATCHED THEN INSERT (TICKER, EARNINGS_DATE, EPS_ESTIMATE, REVENUE_ESTIMATE, FISCAL_YEAR, FISCAL_QUARTER)
                    VALUES (%(ticker)s, %(date)s, %(eps_est)s, %(rev_est)s, %(fy)s, %(fq)s)
                    WHEN MATCHED THEN UPDATE SET EPS_ESTIMATE = %(eps_est)s, REVENUE_ESTIMATE = %(rev_est)s
                    """,
                    {
                        "ticker": ticker,
                        "date": entry.get("date"),
                        "eps_est": entry.get("epsEstimated"),
                        "rev_est": entry.get("revenueEstimated"),
                        "fy": entry.get("fiscalDateEnding", "")[:4] if entry.get("fiscalDateEnding") else None,
                        "fq": None,
                    },
                )
            logger.info(f"Ingested {len(calendar)} earnings dates for {ticker}")
        except Exception as e:
            logger.error(f"Failed to ingest earnings calendar for {ticker}: {e}")


def ingest_analyst_estimates(tickers: list):
    fmp = FMPClient()
    snowflake = SnowflakeClient()

    for ticker in tickers:
        try:
            estimates = fmp.get_analyst_estimates(ticker, limit=12)
            if not estimates:
                continue

            for est in estimates:
                snowflake.execute(
                    """
                    MERGE INTO CONSENSUS_ESTIMATES AS target
                    USING (SELECT %(ticker)s AS TICKER, %(period)s AS PERIOD) AS source
                    ON target.TICKER = source.TICKER AND target.PERIOD = source.PERIOD
                    WHEN NOT MATCHED THEN INSERT (TICKER, PERIOD, EPS_AVG, EPS_HIGH, EPS_LOW, REVENUE_AVG, REVENUE_HIGH, REVENUE_LOW)
                    VALUES (%(ticker)s, %(period)s, %(eps_avg)s, %(eps_high)s, %(eps_low)s, %(rev_avg)s, %(rev_high)s, %(rev_low)s)
                    WHEN MATCHED THEN UPDATE SET EPS_AVG = %(eps_avg)s, EPS_HIGH = %(eps_high)s, EPS_LOW = %(eps_low)s,
                        REVENUE_AVG = %(rev_avg)s, REVENUE_HIGH = %(rev_high)s, REVENUE_LOW = %(rev_low)s
                    """,
                    {
                        "ticker": ticker,
                        "period": est.get("date", ""),
                        "eps_avg": est.get("estimatedEpsAvg"),
                        "eps_high": est.get("estimatedEpsHigh"),
                        "eps_low": est.get("estimatedEpsLow"),
                        "rev_avg": est.get("estimatedRevenueAvg"),
                        "rev_high": est.get("estimatedRevenueHigh"),
                        "rev_low": est.get("estimatedRevenueLow"),
                    },
                )
            logger.info(f"Ingested {len(estimates)} estimate periods for {ticker}")
        except Exception as e:
            logger.error(f"Failed to ingest estimates for {ticker}: {e}")
