import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")

    SNOWFLAKE_USER: str = os.getenv("SNOWFLAKE_USER", "")
    SNOWFLAKE_PASSWORD: str = os.getenv("SNOWFLAKE_PASSWORD", "")
    SNOWFLAKE_ACCOUNT: str = os.getenv("SNOWFLAKE_ACCOUNT", "")
    SNOWFLAKE_WAREHOUSE: str = os.getenv("SNOWFLAKE_WAREHOUSE", "")
    SNOWFLAKE_DATABASE: str = os.getenv("SNOWFLAKE_DATABASE", "EARNINGS_INTEL")
    SNOWFLAKE_SCHEMA: str = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")

    PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY", "")
    PINECONE_TRANSCRIPTS_INDEX: str = os.getenv("PINECONE_TRANSCRIPTS_INDEX", "earnings-transcripts")
    PINECONE_FILINGS_INDEX: str = os.getenv("PINECONE_FILINGS_INDEX", "sec-filings")

    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
    FMP_API_KEY: str = os.getenv("FMP_API_KEY", "")

    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_TLS: bool = os.getenv("REDIS_URL", "").startswith("rediss://")
    CHARTS_DIR: str = os.getenv("CHARTS_DIR", "/tmp/charts")

    SEC_RSS_POLL_INTERVAL: int = int(os.getenv("SEC_RSS_POLL_INTERVAL", "60"))
    MAX_CRITIQUE_ROUNDS: int = int(os.getenv("MAX_CRITIQUE_ROUNDS", "2"))


settings = Settings()
