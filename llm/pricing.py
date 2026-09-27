"""Cost accounting from ``config/model_pricing.yaml``."""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from llm.types import LLMError, Usage

MILLION = Decimal(1_000_000)
Provider = Literal["anthropic", "openai", "scripted"]


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Provider
    input_per_mtok: Decimal = Field(ge=0)
    output_per_mtok: Decimal = Field(ge=0)
    as_of: str | None = None
    source: str | None = None

    def cost(self, usage: Usage) -> Decimal:
        raw = (
            Decimal(usage.input_tokens) * self.input_per_mtok
            + Decimal(usage.output_tokens) * self.output_per_mtok
        ) / MILLION
        return raw.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


class PricingTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: Literal["USD"]
    models: dict[str, ModelPrice]

    @classmethod
    def load(cls, path: Path) -> "PricingTable":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def price(self, model: str) -> ModelPrice:
        try:
            return self.models[model]
        except KeyError as exc:
            raise LLMError(
                "unpriced_model",
                f"model {model!r} has no entry in model_pricing.yaml; add its prices so the "
                "cost budget can be enforced",
            ) from exc
