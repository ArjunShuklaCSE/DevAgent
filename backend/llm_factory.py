"""Build the provider client for the configured model (the only place keys are read)."""

from pathlib import Path

from backend.config import Settings
from llm.anthropic_adapter import AnthropicClient
from llm.client import LLMClient
from llm.openai_adapter import OpenAIClient
from llm.pricing import PricingTable
from llm.types import LLMError


def load_pricing(settings: Settings) -> PricingTable:
    return PricingTable.load(Path(settings.llm_pricing_path))


def build_llm_client(settings: Settings, pricing: PricingTable) -> LLMClient:
    """Return the adapter for ``settings.llm_model``. Fails clearly if not configured."""
    if not settings.llm_model:
        raise LLMError(
            "llm_not_configured", "set DEVAGENT_LLM_MODEL to a model in the pricing file"
        )
    provider = pricing.price(settings.llm_model).provider
    if provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise LLMError("llm_not_configured", "DEVAGENT_ANTHROPIC_API_KEY is not set")
        return AnthropicClient(settings.anthropic_api_key, base_url=settings.anthropic_base_url)
    if provider == "openai":
        if settings.openai_api_key is None:
            raise LLMError("llm_not_configured", "DEVAGENT_OPENAI_API_KEY is not set")
        return OpenAIClient(settings.openai_api_key, base_url=settings.openai_base_url)
    raise LLMError(
        "llm_not_configured",
        f"model {settings.llm_model!r} uses provider {provider!r}, which needs a test script",
    )
