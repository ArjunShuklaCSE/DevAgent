from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from backend.config import Settings
from backend.llm_factory import build_llm_client, load_pricing
from llm.anthropic_adapter import AnthropicClient
from llm.openai_adapter import OpenAIClient
from llm.pricing import ModelPrice, PricingTable
from llm.types import LLMError

ROOT = Path(__file__).parents[2]
TABLE = PricingTable(
    currency="USD",
    models={
        "a-model": ModelPrice(provider="anthropic", input_per_mtok=1, output_per_mtok=1),  # type: ignore[arg-type]
        "o-model": ModelPrice(provider="openai", input_per_mtok=1, output_per_mtok=1),  # type: ignore[arg-type]
    },
)


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


def test_selects_adapter_from_the_pricing_entry() -> None:
    anthropic = build_llm_client(
        settings(llm_model="a-model", anthropic_api_key=SecretStr("k")), TABLE
    )
    assert isinstance(anthropic, AnthropicClient)
    openai = build_llm_client(settings(llm_model="o-model", openai_api_key=SecretStr("k")), TABLE)
    assert isinstance(openai, OpenAIClient)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"llm_model": "a-model"},
        {"llm_model": "o-model"},
        {"llm_model": "not-priced", "openai_api_key": SecretStr("k")},
    ],
)
def test_missing_configuration_fails_clearly(values: dict[str, Any]) -> None:
    with pytest.raises(LLMError) as info:
        build_llm_client(settings(**values), TABLE)
    assert info.value.code in {"llm_not_configured", "unpriced_model"}


def test_keys_are_secret_in_settings_repr() -> None:
    config = settings(anthropic_api_key=SecretStr("sk-super-secret"))
    assert "sk-super-secret" not in repr(config)


def test_default_pricing_file_loads() -> None:
    table = load_pricing(settings(llm_pricing_path=str(ROOT / "config" / "model_pricing.yaml")))
    assert "scripted" in table.models
