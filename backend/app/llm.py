"""LLM router: per-task model choice, strict structured outputs, budgets, fallbacks, usage/cost ledger."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.logging import get_logger, redact

log = get_logger("llm")

T = TypeVar("T", bound=BaseModel)
Task = Literal["fast", "analyst", "critic", "judge"]

# USD per 1M tokens (input, cached input, output). Verified 2026-11-20 against provider pricing pages.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6.2-astra": (8.0, 0.40, 40.0),
    "gpt-6.2-sol": (2.0, 0.10, 10.0),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "claude-opus-5-5": (4.0, 0.40, 20.0),
    "claude-sonnet-5-5": (2.0, 0.20, 10.0),
    "claude-haiku-5-5": (0.10, 0.01, 0.50),
}
FALLBACK = {
    "gpt-6.2-sol": "claude-sonnet-5-5",
    "gpt-6-luna": "claude-haiku-5-5",
    "claude-sonnet-5-5": "gpt-6.2-sol",
    "claude-haiku-5-5": "gpt-6-luna",
}


class LLMUnavailable(Exception):
    pass


class BudgetExceeded(Exception):
    pass


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    by_task: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, task: str, model: str, tin: int, tcached: int, tout: int) -> float:
        pin, pcached, pout = PRICES.get(model, (2.0, 0.2, 10.0))
        cost = ((tin - tcached) * pin + tcached * pcached + tout * pout) / 1_000_000
        self.tokens_in += tin
        self.tokens_cached += tcached
        self.tokens_out += tout
        self.cost_usd += cost
        self.calls += 1
        t = self.by_task.setdefault(
            f"{task}:{model}", {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        )
        t["calls"] += 1
        t["tokens_in"] += tin
        t["tokens_out"] += tout
        t["cost_usd"] += cost
        return cost


class LLMRouter:
    def __init__(
        self,
        settings: Settings | None = None,
        token_budget: int | None = None,
        cost_budget: float | None = None,
        enabled: bool = True,
    ) -> None:
        self.s = settings or get_settings()
        self.enabled = enabled and bool(self.s.OPENAI_API_KEY or self.s.ANTHROPIC_API_KEY)
        self.usage = Usage()
        self.token_budget = token_budget or self.s.RUN_TOKEN_BUDGET
        self.cost_budget = cost_budget or self.s.RUN_COST_BUDGET_USD
        self._lock = threading.Lock()
        self._openai: Any = None
        self._anthropic: Any = None

    def model_for(self, task: Task) -> str:
        return {
            "fast": self.s.MODEL_FAST,
            "analyst": self.s.MODEL_ANALYST,
            "critic": self.s.MODEL_CRITIC,
            "judge": self.s.MODEL_JUDGE,
        }[task]

    def remaining_cost(self) -> float:
        return self.cost_budget - self.usage.cost_usd

    def _check_budget(self, est_in: int) -> None:
        if self.usage.tokens_in + self.usage.tokens_out + est_in > self.token_budget:
            raise BudgetExceeded(f"token budget {self.token_budget} would be exceeded")
        if self.usage.cost_usd >= self.cost_budget:
            raise BudgetExceeded(f"cost budget ${self.cost_budget:.2f} reached")

    def structured(
        self,
        task: Task,
        system: str,
        user: str,
        schema: type[T],
        max_output_tokens: int = 4000,
        cache_key: str | None = None,
    ) -> T:
        if not self.enabled:
            raise LLMUnavailable("no LLM key configured (fixture mode)")
        est_in = (len(system) + len(user)) // 4
        with self._lock:
            self._check_budget(est_in)
        model = self.model_for(task)
        last_err: Exception | None = None
        for attempt, m in enumerate([model, FALLBACK.get(model)]):
            if m is None or not self._provider_ok(m):
                continue
            try:
                start = time.perf_counter()
                obj, tin, tcached, tout = self._call(m, system, user, schema, max_output_tokens, cache_key)
                with self._lock:
                    cost = self.usage.add(task, m, tin, tcached, tout)
                log.info(
                    "llm_call",
                    task=task,
                    model=m,
                    tokens_in=tin,
                    tokens_cached=tcached,
                    tokens_out=tout,
                    cost_usd=round(cost, 5),
                    ms=round((time.perf_counter() - start) * 1000),
                    fallback=attempt > 0,
                )
                return obj
            except BudgetExceeded:
                raise
            except Exception as e:
                last_err = e
                log.warning("llm_call_failed", task=task, model=m, error=redact(repr(e))[:300])
        raise LLMUnavailable(
            f"all models failed for {task}: {type(last_err).__name__ if last_err else 'none'}"
        )

    def _provider_ok(self, model: str) -> bool:
        return bool(self.s.ANTHROPIC_API_KEY) if model.startswith("claude") else bool(self.s.OPENAI_API_KEY)

    def _call(
        self, model: str, system: str, user: str, schema: type[T], max_out: int, cache_key: str | None
    ) -> tuple[T, int, int, int]:
        if model.startswith("claude"):
            return self._call_anthropic(model, system, user, schema, max_out)
        if self._openai is None:
            import openai

            self._openai = openai.OpenAI(api_key=self.s.OPENAI_API_KEY, timeout=120, max_retries=2)
        kwargs: dict[str, Any] = {}
        if cache_key:
            kwargs["prompt_cache_key"] = cache_key
        r = self._openai.responses.parse(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text_format=schema,
            max_output_tokens=max_out,
            store=False,
            **kwargs,
        )
        if r.output_parsed is None:
            raise ValueError("model returned no parsed output (refusal or truncation)")
        u = r.usage
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        return r.output_parsed, u.input_tokens, cached, u.output_tokens

    def _call_anthropic(
        self, model: str, system: str, user: str, schema: type[T], max_out: int
    ) -> tuple[T, int, int, int]:
        if self._anthropic is None:
            import anthropic

            self._anthropic = anthropic.Anthropic(
                api_key=self.s.ANTHROPIC_API_KEY, timeout=120, max_retries=2
            )
        tool = {
            "name": "emit",
            "description": "Return the result",
            "input_schema": schema.model_json_schema(),
        }
        r = self._anthropic.messages.create(
            model=model,
            max_tokens=max_out,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit"},
        )
        block = next(b for b in r.content if getattr(b, "type", "") == "tool_use")
        u = r.usage
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        return schema.model_validate(block.input), u.input_tokens + cached, cached, u.output_tokens
