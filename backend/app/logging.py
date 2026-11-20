"""Structured logging with secret redaction and request-id context."""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # bearer tokens first, so "Authorization: Bearer x" redacts the token rather than the scheme word
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+"), r"\1REDACTED"),
    # key=value / "key": "value" for secret-looking names (query strings, JSON, headers)
    (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret|password|authorization|x-api-key)"
            r"(\"?\s*[=:]\s*\"?)([^\s&\"',]+)"
        ),
        r"\1\2REDACTED",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), "REDACTED"),
    (re.compile(r"\btvly-[A-Za-z0-9_\-]{8,}"), "REDACTED"),
    # credentials embedded in connection URLs
    (re.compile(r"((?:rediss?|postgres(?:ql)?(?:\+\w+)?)://[^:@/\s]*:)[^@\s]+@"), r"\1REDACTED@"),
]


def redact(text: str) -> str:
    """Remove credentials from any string (URLs, headers, exception messages)."""
    if not text:
        return text
    out = text
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _redact_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for k, v in list(event_dict.items()):
        if isinstance(v, str):
            event_dict[k] = redact(v)
    return event_dict


def _ctx_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("request_id", request_id_var.get())
    rid = run_id_var.get()
    if rid != "-":
        event_dict.setdefault("run_id", rid)
    return event_dict


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _ctx_processor,
        _redact_processor,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RedactingFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.handlers = [handler]
    for noisy in ("httpx", "httpcore", "yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str = "app") -> Any:
    return structlog.get_logger(name)
