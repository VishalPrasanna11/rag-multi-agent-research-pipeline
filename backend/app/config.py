"""Typed settings. Every key is optional; missing keys degrade features to fixture/unavailable."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

SCHEMA_VERSION = "2026-11-20.1"
APP_VERSION = "3.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", case_sensitive=True)

    ENV: str = "development"  # development | test | production
    GIT_SHA: str = Field(default="dev", validation_alias="SOURCE_VERSION")
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # Fixture mode: canned data, no external keys. Forced on when no LLM key exists.
    FIXTURE_MODE: bool = False

    # Storage
    DATABASE_URL: str = ""
    REDIS_URL: str = ""

    # Auth
    AUTH_JWKS_URL: str = ""
    AUTH_ISSUER: str = ""
    AUTH_AUDIENCE: str = ""
    CLERK_PUBLISHABLE_KEY: str = ""
    ALLOW_ANONYMOUS: bool = True
    SESSION_SECRET: str = "dev-insecure-session-secret-change-me"  # noqa: S105 - dev default only

    # LLM
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    MODEL_FAST: str = "gpt-6-luna"
    MODEL_ANALYST: str = "gpt-6.2-sol"
    MODEL_CRITIC: str = "gpt-6.2-sol"
    MODEL_JUDGE: str = "gpt-6.2-sol"
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    RUN_TOKEN_BUDGET: int = 80_000
    RUN_COST_BUDGET_USD: float = 0.30
    MAX_CRITIQUE_ROUNDS: int = 1  # refine rounds; one more critique pass always records residual issues

    # Data providers
    FMP_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    FRED_API_KEY: str = ""
    FINNHUB_API_KEY: str = ""
    SEC_USER_AGENT: str = "EarningsIntelPlatform/3.0 (set SEC_USER_AGENT to 'App contact@email')"
    SEC_MAX_RPS: float = 8.0
    ENABLE_YFINANCE: bool = True

    # Vectors / warehouse
    PINECONE_API_KEY: str = ""
    PINECONE_FILINGS_INDEX: str = "sec-filings"
    MARKET_STORE: str = "appdb"  # appdb | snowflake
    SNOWFLAKE_USER: str = ""
    SNOWFLAKE_PASSWORD: str = ""
    SNOWFLAKE_ACCOUNT: str = ""

    # Observability
    SENTRY_DSN: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

    ADMIN_SUBS: str = ""  # comma-separated JWT subs allowed to see global metrics

    # Rate limits (per identity)
    RUNS_PER_HOUR: int = 20
    ANON_RUNS_PER_HOUR: int = 5

    # Eval-only hooks (never honoured in production)
    EVAL_HOOKS: bool = False

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def fixture_mode(self) -> bool:
        return self.FIXTURE_MODE or not (self.OPENAI_API_KEY or self.ANTHROPIC_API_KEY)

    @property
    def database_url(self) -> str:
        url = self.DATABASE_URL
        if not url:
            return f"sqlite:///{ROOT / 'var' / 'app.db'}" if self.ENV != "test" else "sqlite://"
        if url.startswith("sqlite"):
            return url
        if url.startswith("postgres://"):  # Heroku legacy scheme
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @property
    def durable_storage(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def auth_configured(self) -> bool:
        return bool(self.AUTH_JWKS_URL and self.AUTH_ISSUER)

    def missing_keys(self) -> dict[str, str]:
        """Feature -> missing setting, for the 'needs <key>' UI and docs."""
        out: dict[str, str] = {}
        if not (self.OPENAI_API_KEY or self.ANTHROPIC_API_KEY):
            out["llm"] = "OPENAI_API_KEY or ANTHROPIC_API_KEY"
        if not self.ANTHROPIC_API_KEY:
            out["cross_provider_eval"] = "ANTHROPIC_API_KEY"
        if not self.FMP_API_KEY:
            out["consensus"] = "FMP_API_KEY"
        if not self.FINNHUB_API_KEY:
            out["estimate_dispersion"] = "FINNHUB_API_KEY or FMP premium"
        if not self.FRED_API_KEY:
            out["macro"] = "FRED_API_KEY"
        if not self.TAVILY_API_KEY:
            out["news"] = "TAVILY_API_KEY"
        if not self.durable_storage:
            out["durable_storage"] = "DATABASE_URL (Heroku Postgres)"
        if not self.auth_configured:
            out["auth"] = "AUTH_JWKS_URL + AUTH_ISSUER (Clerk)"
        if "set SEC_USER_AGENT" in self.SEC_USER_AGENT:
            out["sec_contact"] = "SEC_USER_AGENT"
        out["transcripts"] = "licensed transcript source (upload supported)"
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
