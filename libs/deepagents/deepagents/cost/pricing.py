"""Model pricing data for cost calculation.

Prices are per-token (not per-million-tokens) to avoid repeated division
at calculation time. Source: OpenRouter published pricing as of 2026-04.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Per-token pricing for a single model.

    Attributes:
        input: Cost per input token in USD.
        output: Cost per output token in USD.
        cached: Cost per cached input token in USD.
    """

    input: float
    output: float
    cached: float


def _per_token(input_per_m: float, output_per_m: float, cached_per_m: float) -> ModelPricing:
    """Convert per-million-token prices to per-token prices.

    Args:
        input_per_m: Cost per 1M input tokens in USD.
        output_per_m: Cost per 1M output tokens in USD.
        cached_per_m: Cost per 1M cached input tokens in USD.

    Returns:
        A `ModelPricing` instance with per-token costs.
    """
    return ModelPricing(
        input=input_per_m / 1_000_000,
        output=output_per_m / 1_000_000,
        cached=cached_per_m / 1_000_000,
    )


# Default pricing table keyed by OpenRouter model identifier.
# Cached pricing is typically 50-90% off input pricing depending on provider.
DEFAULT_PRICING: dict[str, ModelPricing] = {
    "deepseek/deepseek-chat": _per_token(0.14, 0.28, 0.014),
    "meta-llama/llama-3.3-70b": _per_token(0.39, 0.40, 0.20),
    "qwen/qwen-2.5-72b": _per_token(0.36, 0.40, 0.18),
    "anthropic/claude-sonnet-4-6": _per_token(3.00, 15.00, 0.30),
    "anthropic/claude-haiku-4-5": _per_token(0.80, 4.00, 0.08),
    "openai/gpt-4o": _per_token(2.50, 10.00, 1.25),
    "openai/gpt-4o-mini": _per_token(0.15, 0.60, 0.075),
    "google/gemini-2.5-flash": _per_token(0.15, 0.60, 0.0375),
}
