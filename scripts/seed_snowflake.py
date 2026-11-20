"""Create Snowflake tables for the Earnings Intelligence Platform."""
import sys
sys.path.insert(0, "backend")

from tools.snowflake_client import SnowflakeClient

DDL = [
    """
    CREATE TABLE IF NOT EXISTS STOCK_PRICES (
        TICKER VARCHAR(10),
        TRADE_DATE DATE,
        OPEN FLOAT,
        HIGH FLOAT,
        LOW FLOAT,
        CLOSE FLOAT,
        VOLUME BIGINT,
        PRIMARY KEY (TICKER, TRADE_DATE)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS CONSENSUS_ESTIMATES (
        TICKER VARCHAR(10),
        PERIOD VARCHAR(20),
        EPS_AVG FLOAT,
        EPS_HIGH FLOAT,
        EPS_LOW FLOAT,
        REVENUE_AVG FLOAT,
        REVENUE_HIGH FLOAT,
        REVENUE_LOW FLOAT,
        PRIMARY KEY (TICKER, PERIOD)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS EARNINGS_CALENDAR (
        TICKER VARCHAR(10),
        EARNINGS_DATE DATE,
        EPS_ESTIMATE FLOAT,
        REVENUE_ESTIMATE FLOAT,
        FISCAL_YEAR VARCHAR(4),
        FISCAL_QUARTER VARCHAR(2),
        PRIMARY KEY (TICKER, EARNINGS_DATE)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS EARNINGS_ACTUALS (
        TICKER VARCHAR(10),
        FISCAL_YEAR INT,
        FISCAL_QUARTER INT,
        METRIC_NAME VARCHAR(50),
        ACTUAL_VALUE FLOAT,
        REPORTED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
        PRIMARY KEY (TICKER, FISCAL_YEAR, FISCAL_QUARTER, METRIC_NAME)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ANALYST_REVISIONS (
        TICKER VARCHAR(10),
        ANALYST VARCHAR(100),
        ACTION VARCHAR(20),
        OLD_TARGET FLOAT,
        NEW_TARGET FLOAT,
        REVISION_DATE DATE,
        PRIMARY KEY (TICKER, ANALYST, REVISION_DATE)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS COMPANY_FUNDAMENTALS (
        TICKER VARCHAR(10),
        FISCAL_YEAR INT,
        FISCAL_QUARTER INT,
        REVENUE FLOAT,
        NET_INCOME FLOAT,
        EPS FLOAT,
        GROSS_MARGIN FLOAT,
        OPERATING_MARGIN FLOAT,
        FREE_CASH_FLOW FLOAT,
        PRIMARY KEY (TICKER, FISCAL_YEAR, FISCAL_QUARTER)
    )
    """,
]


def main():
    client = SnowflakeClient()
    for ddl in DDL:
        try:
            client.execute(ddl)
            table_name = ddl.split("EXISTS")[1].split("(")[0].strip()
            print(f"Created table: {table_name}")
        except Exception as e:
            print(f"Error: {e}")
    print("Snowflake seed complete.")


if __name__ == "__main__":
    main()
