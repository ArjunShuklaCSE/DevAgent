"""Persist LLM calls (``llm_calls``) and emit ``llm_usage`` events (spec 9, 12)."""

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.ports import RunRecorder
from core.events import LlmUsage
from database.models import AgentRun, LlmCall
from llm.metered import LlmCallLog


class DbLlmCallSink:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        events: RunRecorder | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_id = run_id
        self._events = events

    async def record(self, call: LlmCallLog) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                LlmCall(
                    id=call.llm_call_id,
                    run_id=self._run_id,
                    step_id=uuid.UUID(call.step_id) if call.step_id else None,
                    provider=call.provider,
                    model=call.model,
                    component=call.component,
                    prompt_version=call.prompt_version,
                    attempt=call.attempt,
                    input_tokens=call.usage.input_tokens,
                    output_tokens=call.usage.output_tokens,
                    cost_usd=call.cost_usd,
                    latency_ms=call.latency_ms,
                    status=call.status,
                    rationale=call.rationale,
                    error=call.error,
                )
            )
            # Keep the run's running totals current for the UI (spec 11, metrics cards).
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id == self._run_id)
                .values(
                    input_tokens=AgentRun.input_tokens + call.usage.input_tokens,
                    output_tokens=AgentRun.output_tokens + call.usage.output_tokens,
                    cost_usd=AgentRun.cost_usd + call.cost_usd,
                )
            )
        if self._events is not None:
            await self._events.emit(
                LlmUsage(
                    llm_call_id=call.llm_call_id,
                    model=call.model,
                    input_tokens=call.usage.input_tokens,
                    output_tokens=call.usage.output_tokens,
                    cost_usd=float(call.cost_usd),
                )
            )
