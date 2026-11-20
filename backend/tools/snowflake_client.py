import logging
from typing import Optional, List
import pandas as pd
import snowflake.connector
from config import settings

logger = logging.getLogger(__name__)


class SnowflakeClient:
    def __init__(self):
        self._conn = None

    def _get_connection(self):
        if self._conn is None or self._conn.is_closed():
            self._conn = snowflake.connector.connect(
                user=settings.SNOWFLAKE_USER,
                password=settings.SNOWFLAKE_PASSWORD,
                account=settings.SNOWFLAKE_ACCOUNT,
                warehouse=settings.SNOWFLAKE_WAREHOUSE,
                database=settings.SNOWFLAKE_DATABASE,
                schema=settings.SNOWFLAKE_SCHEMA,
            )
        return self._conn

    def execute_query(self, query: str, params: Optional[dict] = None) -> pd.DataFrame:
        conn = self._get_connection()
        try:
            return pd.read_sql(query, conn, params=params)
        except Exception as e:
            logger.error(f"Snowflake query error: {e}")
            raise

    def get_stock_prices(self, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None) -> pd.DataFrame:
        query = "SELECT * FROM STOCK_PRICES WHERE TICKER = %(ticker)s"
        params = {"ticker": ticker}
        if years:
            placeholders = ", ".join([f"%(y{i})s" for i in range(len(years))])
            query += f" AND YEAR(DATE) IN ({placeholders})"
            for i, y in enumerate(years):
                params[f"y{i}"] = y
        if quarters:
            placeholders = ", ".join([f"%(q{i})s" for i in range(len(quarters))])
            query += f" AND QUARTER(DATE) IN ({placeholders})"
            for i, q in enumerate(quarters):
                params[f"q{i}"] = q
        query += " ORDER BY DATE"
        return self.execute_query(query, params)

    def get_consensus_estimates(self, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None) -> pd.DataFrame:
        query = "SELECT * FROM CONSENSUS_ESTIMATES WHERE TICKER = %(ticker)s"
        params = {"ticker": ticker}
        if years:
            placeholders = ", ".join([f"%(y{i})s" for i in range(len(years))])
            query += f" AND FISCAL_YEAR IN ({placeholders})"
            for i, y in enumerate(years):
                params[f"y{i}"] = y
        if quarters:
            placeholders = ", ".join([f"%(q{i})s" for i in range(len(quarters))])
            query += f" AND FISCAL_QUARTER IN ({placeholders})"
            for i, q in enumerate(quarters):
                params[f"q{i}"] = q
        query += " ORDER BY FISCAL_YEAR, FISCAL_QUARTER"
        return self.execute_query(query, params)

    def get_earnings_calendar(self, ticker: Optional[str] = None, days_ahead: int = 30) -> pd.DataFrame:
        query = """
            SELECT * FROM EARNINGS_CALENDAR
            WHERE EARNINGS_DATE BETWEEN CURRENT_DATE AND DATEADD(day, %(days)s, CURRENT_DATE)
        """
        params = {"days": days_ahead}
        if ticker:
            query += " AND TICKER = %(ticker)s"
            params["ticker"] = ticker
        query += " ORDER BY EARNINGS_DATE"
        return self.execute_query(query, params)

    def get_company_fundamentals(self, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None) -> pd.DataFrame:
        query = "SELECT * FROM COMPANY_FUNDAMENTALS WHERE TICKER = %(ticker)s"
        params = {"ticker": ticker}
        if years:
            placeholders = ", ".join([f"%(y{i})s" for i in range(len(years))])
            query += f" AND FISCAL_YEAR IN ({placeholders})"
            for i, y in enumerate(years):
                params[f"y{i}"] = y
        if quarters:
            placeholders = ", ".join([f"%(q{i})s" for i in range(len(quarters))])
            query += f" AND FISCAL_QUARTER IN ({placeholders})"
            for i, q in enumerate(quarters):
                params[f"q{i}"] = q
        query += " ORDER BY FISCAL_YEAR, FISCAL_QUARTER"
        return self.execute_query(query, params)

    def get_analyst_revisions(self, ticker: str, days_back: int = 30) -> pd.DataFrame:
        query = """
            SELECT * FROM ANALYST_REVISIONS
            WHERE TICKER = %(ticker)s
            AND REVISION_DATE >= DATEADD(day, -%(days)s, CURRENT_DATE)
            ORDER BY REVISION_DATE DESC
        """
        return self.execute_query(query, {"ticker": ticker, "days": days_back})

    def write_actuals(self, ticker: str, fiscal_year: int, fiscal_quarter: int, metric: str, actual: float):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE CONSENSUS_ESTIMATES
            SET ACTUAL = %(actual)s, SURPRISE_PCT = ((%(actual)s - ESTIMATE) / ABS(ESTIMATE)) * 100
            WHERE TICKER = %(ticker)s AND FISCAL_YEAR = %(fy)s AND FISCAL_QUARTER = %(fq)s AND METRIC = %(metric)s
            """,
            {"actual": actual, "ticker": ticker, "fy": fiscal_year, "fq": fiscal_quarter, "metric": metric},
        )
        conn.commit()

    def run_cortex_forecast(self, ticker: str, metric: str = "EPS") -> pd.DataFrame:
        query = """
            SELECT * FROM TABLE(
                SNOWFLAKE.CORTEX.FORECAST(
                    INPUT_DATA => SYSTEM$QUERY_REFERENCE(
                        'SELECT FISCAL_YEAR * 10 + FISCAL_QUARTER AS PERIOD, ACTUAL AS VALUE
                         FROM CONSENSUS_ESTIMATES
                         WHERE TICKER = \\\'""" + ticker + """\\\' AND METRIC = \\\'""" + metric + """\\\' AND ACTUAL IS NOT NULL
                         ORDER BY PERIOD'
                    ),
                    TIMESTAMP_COLNAME => 'PERIOD',
                    TARGET_COLNAME => 'VALUE',
                    HORIZON => 4
                )
            )
        """
        return self.execute_query(query)

    def run_cortex_sentiment(self, text: str) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT SNOWFLAKE.CORTEX.SENTIMENT(%(text)s) AS SENTIMENT", {"text": text})
        row = cursor.fetchone()
        return row[0] if row else "neutral"

    def health_check(self) -> bool:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return True
        except Exception:
            return False

    def close(self):
        if self._conn and not self._conn.is_closed():
            self._conn.close()
