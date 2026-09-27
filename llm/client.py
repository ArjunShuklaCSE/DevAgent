"""The ``LLMClient`` interface every adapter implements."""

from typing import Protocol

from llm.types import LLMRequest, LLMResponse


class LLMClient(Protocol):
    provider: str

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send one request. Raises ``LLMError`` on provider or transport failure."""
        ...
