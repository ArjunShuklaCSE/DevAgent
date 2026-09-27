"""Every LLM call, including refused ones, is recorded in ``llm_calls`` (Phase 5)."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.event_bus import InMemoryEventBus
from backend.services.event_store import SqlEventReader
from backend.services.llm_log import DbLlmCallSink
from backend.services.recorder import DbRunRecorder
from core.tools import CallStatus
from database.models import LlmCall
from llm.budget import BudgetExceededError, BudgetLimits, BudgetTracker
from llm.metered import MeteredLLMClient
from llm.pricing import ModelPrice, PricingTable
from llm.scripted import ScriptedLLM, ScriptedToolCall, ScriptStep
from llm.structured import StructuredOutput, complete_structured
from llm.types import LLMRequest, Message
from tests.integration.test_event_store import _new_run, session_factory

__all__ = ["session_factory"]  # fixture

pytestmark = pytest.mark.integration

PRICING = PricingTable(
    currency="USD",
    models={
        "m": ModelPrice(provider="scripted", input_per_mtok=Decimal(3), output_per_mtok=Decimal(15))
    },
)


class Answer(StructuredOutput):
    value: int


async def test_llm_calls_are_persisted_with_cost_attempts_and_refusals(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _new_run(session_factory)
    recorder = DbRunRecorder(session_factory, InMemoryEventBus(), run.id)
    scripted = ScriptedLLM(
        [
            ScriptStep(
                tool_calls=[
                    ScriptedToolCall(name="submit", input={"rationale": "r", "value": "x"})
                ],
                input_tokens=2000,
                output_tokens=100,
            ),
            ScriptStep(
                tool_calls=[
                    ScriptedToolCall(name="submit", input={"rationale": "It is 4.", "value": 4})
                ],
                input_tokens=2100,
                output_tokens=120,
            ),
        ]
    )
    tracker = BudgetTracker(BudgetLimits(max_cost_usd=Decimal("0.05")))
    client = MeteredLLMClient(
        scripted, PRICING, tracker, str(run.id), DbLlmCallSink(session_factory, run.id, recorder)
    )
    request = LLMRequest(
        model="m",
        system="s",
        messages=[Message.user("2+2?")],
        max_tokens=1000,
        metadata={"component": "issue_analyzer", "prompt_version": "v1:0123456789ab"},
    )
    result = await complete_structured(client, request, Answer)
    assert result.value.value == 4

    # Remaining budget: $0.05 - $0.01695 spent; a 3000-token reply could cost $0.045.
    with pytest.raises(BudgetExceededError):
        await client.complete(request.model_copy(update={"max_tokens": 3000}))

    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(LlmCall).where(LlmCall.run_id == run.id).order_by(LlmCall.created_at)
            )
        ).all()
    assert [(r.attempt, r.status) for r in rows] == [
        (1, CallStatus.OK),
        (2, CallStatus.OK),
        (1, CallStatus.DENIED),
    ]
    assert rows[0].component == "issue_analyzer"
    assert rows[0].prompt_version == "v1:0123456789ab"
    assert rows[0].provider == "scripted"
    assert (rows[1].input_tokens, rows[1].output_tokens) == (2100, 120)
    assert rows[0].cost_usd == Decimal("0.007500")  # 2000*$3 + 100*$15 per Mtok
    assert rows[1].cost_usd == Decimal("0.008100")
    assert rows[1].rationale == "It is 4."
    assert rows[2].error is not None
    assert rows[2].error["code"] == "budget_cost"
    assert tracker.cost_usd == Decimal("0.015600")

    page = await SqlEventReader(session_factory).read_after(run.id, 0, limit=10)
    assert page is not None
    assert [e.event_type for e in page.events] == ["llm_usage"] * 3
    assert page.events[1].payload["cost_usd"] == pytest.approx(0.0081)
