"""``MeteredLLMClient``: budget checks, cost accounting and logging around any client.

Every call is checked against the run budget *before* it is sent, priced from
``model_pricing.yaml`` and recorded through an ``LlmCallSink`` (``llm_calls`` rows and
``llm_usage`` events in the backend), including calls that fail or are refused.
"""

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import structlog

from core.tools import CallStatus
from llm.budget import BudgetExceededError, BudgetTracker
from llm.client import LLMClient
from llm.pricing import PricingTable
from llm.types import LLMError, LLMRequest, LLMResponse, Usage

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LlmCallLog:
    llm_call_id: uuid.UUID
    run_id: str
    step_id: str | None
    provider: str
    model: str
    component: str
    prompt_version: str
    attempt: int
    usage: Usage
    cost_usd: Decimal
    latency_ms: int
    status: CallStatus
    rationale: str | None
    error: dict[str, Any] | None


class LlmCallSink(Protocol):
    async def record(self, call: LlmCallLog) -> None: ...


class NullLlmSink:
    async def record(self, call: LlmCallLog) -> None:
        return None


class MeteredLLMClient:
    def __init__(
        self,
        inner: LLMClient,
        pricing: PricingTable,
        budget: BudgetTracker,
        run_id: str,
        sink: LlmCallSink | None = None,
    ) -> None:
        self._inner = inner
        self._pricing = pricing
        self._budget = budget
        self._run_id = run_id
        self._sink = sink or NullLlmSink()
        self.provider = inner.provider

    @property
    def budget(self) -> BudgetTracker:
        return self._budget

    async def complete(self, request: LLMRequest) -> LLMResponse:
        call_id = uuid.uuid4()
        price = self._pricing.price(request.model)
        started = time.monotonic()
        try:
            self._budget.before_llm_call(request, price)
        except BudgetExceededError as exc:
            await self._record(
                call_id=call_id,
                request=request,
                usage=Usage(),
                cost=Decimal(0),
                started=started,
                status=CallStatus.DENIED,
                rationale=None,
                error={"code": f"budget_{exc.limit}", "message": str(exc)},
            )
            raise
        try:
            response = await self._inner.complete(request)
        except LLMError as exc:
            await self._record(
                call_id=call_id,
                request=request,
                usage=Usage(),
                cost=Decimal(0),
                started=started,
                status=CallStatus.ERROR,
                rationale=None,
                error={"code": exc.code, "message": str(exc)},
            )
            raise
        cost = price.cost(response.usage)
        self._budget.record_llm_usage(response.usage, cost)
        await self._record(
            call_id=call_id,
            request=request,
            usage=response.usage,
            cost=cost,
            started=started,
            status=CallStatus.OK,
            rationale=_rationale(response),
            error=None,
        )
        return response

    async def _record(
        self,
        *,
        call_id: uuid.UUID,
        request: LLMRequest,
        usage: Usage,
        cost: Decimal,
        started: float,
        status: CallStatus,
        rationale: str | None,
        error: dict[str, Any] | None,
    ) -> None:
        meta = request.metadata
        log = LlmCallLog(
            llm_call_id=call_id,
            run_id=self._run_id,
            step_id=meta.get("step_id"),
            provider=self._inner.provider,
            model=request.model,
            component=meta.get("component", "unknown"),
            prompt_version=meta.get("prompt_version", "unversioned"),
            attempt=int(meta.get("attempt", "1")),
            usage=usage,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
            status=status,
            rationale=rationale,
            error=error,
        )
        logger.info(
            "llm_call",
            run_id=self._run_id,
            component=log.component,
            model=log.model,
            status=status.value,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=str(cost),
            latency_ms=log.latency_ms,
            error_code=error["code"] if error else None,
        )
        await self._sink.record(log)


def _rationale(response: LLMResponse) -> str | None:
    for block in response.tool_uses:
        value = block.input.get("rationale")
        if isinstance(value, str):
            return value[:2000]
    return None
