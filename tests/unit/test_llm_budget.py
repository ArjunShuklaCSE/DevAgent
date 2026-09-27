"""Cost accounting, budget enforcement and call logging around any client (spec 6.6)."""

from decimal import Decimal
from pathlib import Path

import pytest

from core.tools import CallStatus
from llm.budget import BudgetExceededError, BudgetLimits, BudgetTracker
from llm.metered import LlmCallLog, MeteredLLMClient
from llm.pricing import ModelPrice, PricingTable
from llm.scripted import ScriptedLLM, ScriptStep
from llm.types import LLMError, LLMRequest, Message, Usage

PRICING = PricingTable(
    currency="USD",
    models={
        "cheap": ModelPrice(
            provider="scripted", input_per_mtok=Decimal(1), output_per_mtok=Decimal(5)
        ),
        "pricey": ModelPrice(
            provider="scripted", input_per_mtok=Decimal(300), output_per_mtok=Decimal(1500)
        ),
    },
)


def request(model: str = "cheap", max_tokens: int = 1000, component: str = "planner") -> LLMRequest:
    return LLMRequest(
        model=model,
        system="s",
        messages=[Message.user("hello")],
        max_tokens=max_tokens,
        metadata={"component": component, "prompt_version": "v1:abc", "step_id": ""},
    )


class Sink:
    def __init__(self) -> None:
        self.calls: list[LlmCallLog] = []

    async def record(self, call: LlmCallLog) -> None:
        self.calls.append(call)


def test_pricing_file_loads_and_prices() -> None:
    table = PricingTable.load(Path(__file__).parents[2] / "config" / "model_pricing.yaml")
    assert table.price("scripted").cost(Usage(input_tokens=10**6, output_tokens=10**6)) == 0
    price = ModelPrice(
        provider="openai", input_per_mtok=Decimal("2.00"), output_per_mtok=Decimal("8.00")
    )
    assert price.cost(Usage(input_tokens=1_000_000, output_tokens=500_000)) == Decimal("6.000000")
    assert price.cost(Usage(input_tokens=1234, output_tokens=567)) == Decimal("0.007004")
    with pytest.raises(LLMError) as info:
        table.price("unknown-model")
    assert info.value.code == "unpriced_model"


def test_step_and_fix_attempt_limits() -> None:
    tracker = BudgetTracker(BudgetLimits(max_steps=2, max_fix_attempts=1))
    tracker.before_step()
    tracker.before_step()
    with pytest.raises(BudgetExceededError) as steps:
        tracker.before_step()
    assert steps.value.limit == "steps"
    tracker.before_fix_attempt()
    with pytest.raises(BudgetExceededError) as fixes:
        tracker.before_fix_attempt()
    assert fixes.value.limit == "fix_attempts"


def test_wall_clock_limit() -> None:
    now = [0.0]
    tracker = BudgetTracker(BudgetLimits(wall_clock_seconds=60), clock=lambda: now[0])
    tracker.before_step()
    now[0] = 59.0
    assert tracker.remaining_seconds() == 1.0
    now[0] = 60.0
    with pytest.raises(BudgetExceededError) as info:
        tracker.before_step()
    assert info.value.limit == "wall_clock"


def test_token_and_cost_checks_are_worst_case_before_the_call() -> None:
    tracker = BudgetTracker(BudgetLimits(max_tokens=2000))
    tracker.before_llm_call(request(max_tokens=1000), PRICING.price("cheap"))
    with pytest.raises(BudgetExceededError) as tokens:
        tracker.before_llm_call(request(max_tokens=1999), PRICING.price("cheap"))
    assert tokens.value.limit == "tokens"

    tracker = BudgetTracker(BudgetLimits(max_cost_usd=Decimal("1.00")))
    # 1000 output tokens at $1500/Mtok = $1.50 worst case > $1.00 remaining.
    with pytest.raises(BudgetExceededError) as cost:
        tracker.before_llm_call(request("pricey"), PRICING.price("pricey"))
    assert cost.value.limit == "cost"
    assert "$1.00 remain" in str(cost.value)


async def test_metered_client_records_cost_and_rationale() -> None:
    scripted = ScriptedLLM(
        [
            ScriptStep(
                tool_calls=[{"name": "submit", "input": {"rationale": "Off-by-one in range()."}}],  # type: ignore[list-item]
                input_tokens=1000,
                output_tokens=200,
            )
        ]
    )
    sink = Sink()
    tracker = BudgetTracker(BudgetLimits())
    client = MeteredLLMClient(scripted, PRICING, tracker, run_id="r1", sink=sink)
    await client.complete(request())
    (log,) = sink.calls
    assert log.status is CallStatus.OK
    assert log.component == "planner"
    assert log.prompt_version == "v1:abc"
    assert log.cost_usd == Decimal("0.002000")  # 1000*$1 + 200*$5 per Mtok
    assert log.rationale == "Off-by-one in range()."
    assert tracker.usage == Usage(input_tokens=1000, output_tokens=200)
    assert tracker.cost_usd == Decimal("0.002000")


async def test_metered_client_refuses_over_budget_call_without_sending_it() -> None:
    scripted = ScriptedLLM([ScriptStep(text="never used")])
    sink = Sink()
    tracker = BudgetTracker(BudgetLimits(max_cost_usd=Decimal("0.10")))
    client = MeteredLLMClient(scripted, PRICING, tracker, run_id="r1", sink=sink)
    with pytest.raises(BudgetExceededError):
        await client.complete(request("pricey"))
    assert scripted.requests == []  # the provider was never called
    (log,) = sink.calls
    assert log.status is CallStatus.DENIED
    assert log.error is not None
    assert log.error["code"] == "budget_cost"


async def test_metered_client_records_provider_errors() -> None:
    scripted = ScriptedLLM([])
    sink = Sink()
    client = MeteredLLMClient(scripted, PRICING, BudgetTracker(BudgetLimits()), "r1", sink)
    with pytest.raises(LLMError):
        await client.complete(request())
    assert sink.calls[0].status is CallStatus.ERROR
    assert sink.calls[0].error == {
        "code": "script_exhausted",
        "message": "no scripted response for call 1",
    }


async def test_budget_is_cumulative_across_calls() -> None:
    steps = [ScriptStep(text="ok", input_tokens=600, output_tokens=400) for _ in range(3)]
    tracker = BudgetTracker(BudgetLimits(max_tokens=2600))
    client = MeteredLLMClient(ScriptedLLM(steps), PRICING, tracker, "r1")
    await client.complete(request(max_tokens=500))
    await client.complete(request(max_tokens=500))
    assert tracker.usage.total == 2000
    with pytest.raises(BudgetExceededError):  # 2000 used + ~500 worst case + input > 2600
        await client.complete(request(max_tokens=600))
