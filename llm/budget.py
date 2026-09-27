"""Run budgets (spec 6.6), checked before every action.

The tracker is the single source of truth for what a run has spent. Checks happen
*before* work: a step, a fix attempt or an LLM call that could exceed a limit is not
started, and ``BudgetExceededError`` says which limit and by how much.

For LLM calls the check is conservative. It assumes the reply uses the whole
``max_tokens`` allowance, and it estimates input tokens from the request size
(about 4 characters per token). A call is refused if that worst case would cross the
token or cost limit.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from llm.pricing import ModelPrice
from llm.types import LLMRequest, Usage

BudgetLimit = Literal["steps", "fix_attempts", "tokens", "cost", "wall_clock"]
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class BudgetLimits:
    max_steps: int = 40
    max_fix_attempts: int = 3
    max_tokens: int = 400_000
    max_cost_usd: Decimal = Decimal("2.00")
    command_timeout_seconds: int = 300
    wall_clock_seconds: int = 1800


class BudgetExceededError(Exception):
    def __init__(self, limit: BudgetLimit, message: str) -> None:
        super().__init__(message)
        self.limit = limit


def estimate_input_tokens(request: LLMRequest) -> int:
    size = len(request.system) + len(request.model_dump_json(include={"messages", "tools"}))
    return size // CHARS_PER_TOKEN + 1


class BudgetTracker:
    def __init__(self, limits: BudgetLimits, clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self._clock = clock
        self._started = clock()
        self.steps = 0
        self.fix_attempts = 0
        self.usage = Usage()
        self.cost_usd = Decimal(0)
        self.llm_calls = 0

    # ------------------------------------------------------------------ checks
    def elapsed_seconds(self) -> float:
        return self._clock() - self._started

    def remaining_seconds(self) -> float:
        return max(0.0, self.limits.wall_clock_seconds - self.elapsed_seconds())

    def check_wall_clock(self) -> None:
        if self.elapsed_seconds() >= self.limits.wall_clock_seconds:
            raise BudgetExceededError(
                "wall_clock",
                f"run exceeded its wall-clock limit of {self.limits.wall_clock_seconds}s",
            )

    def before_step(self) -> None:
        self.check_wall_clock()
        if self.steps >= self.limits.max_steps:
            raise BudgetExceededError(
                "steps", f"run reached its limit of {self.limits.max_steps} agent steps"
            )
        self.steps += 1

    def before_fix_attempt(self) -> None:
        self.check_wall_clock()
        if self.fix_attempts >= self.limits.max_fix_attempts:
            raise BudgetExceededError(
                "fix_attempts",
                f"run used all {self.limits.max_fix_attempts} fix attempts",
            )
        self.fix_attempts += 1

    def before_llm_call(self, request: LLMRequest, price: ModelPrice) -> None:
        self.check_wall_clock()
        worst = Usage(input_tokens=estimate_input_tokens(request), output_tokens=request.max_tokens)
        if self.usage.total + worst.total > self.limits.max_tokens:
            raise BudgetExceededError(
                "tokens",
                f"next LLM call could use up to {worst.total} tokens; "
                f"{self.limits.max_tokens - self.usage.total} of {self.limits.max_tokens} remain",
            )
        worst_cost = price.cost(worst)
        if self.cost_usd + worst_cost > self.limits.max_cost_usd:
            raise BudgetExceededError(
                "cost",
                f"next LLM call could cost up to ${worst_cost}; "
                f"${self.limits.max_cost_usd - self.cost_usd} of "
                f"${self.limits.max_cost_usd} remain",
            )

    # ------------------------------------------------------------------ accounting
    def record_llm_usage(self, usage: Usage, cost: Decimal) -> None:
        self.usage = self.usage + usage
        self.cost_usd += cost
        self.llm_calls += 1

    def snapshot(self) -> dict[str, object]:
        return {
            "steps": self.steps,
            "fix_attempts": self.fix_attempts,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cost_usd": str(self.cost_usd),
            "llm_calls": self.llm_calls,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
        }
