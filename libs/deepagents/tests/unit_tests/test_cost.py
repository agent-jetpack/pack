"""Unit tests for the cost tracking module."""

from __future__ import annotations

import pytest

from deepagents.cost import (
    BudgetExceededError,
    CostTracker,
    ModelPricing,
    TurnUsage,
    format_cache_rate,
    format_cost,
    format_tokens,
)
from deepagents.cost.pricing import DEFAULT_PRICING, _per_token

# ---------------------------------------------------------------------------
# pricing.py
# ---------------------------------------------------------------------------


class TestModelPricing:
    def test_frozen(self) -> None:
        p = ModelPricing(input=1e-6, output=2e-6, cached=0.5e-6)
        with pytest.raises(AttributeError):
            p.input = 0.0  # type: ignore[misc]

    def test_per_token_helper(self) -> None:
        p = _per_token(1.0, 2.0, 0.5)
        assert p.input == pytest.approx(1e-6)
        assert p.output == pytest.approx(2e-6)
        assert p.cached == pytest.approx(0.5e-6)

    def test_default_pricing_has_expected_models(self) -> None:
        expected = {
            "deepseek/deepseek-chat",
            "meta-llama/llama-3.3-70b",
            "qwen/qwen-2.5-72b",
            "anthropic/claude-sonnet-4-6",
            "anthropic/claude-haiku-4-5",
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "google/gemini-2.5-flash",
        }
        assert expected == set(DEFAULT_PRICING.keys())


# ---------------------------------------------------------------------------
# display.py
# ---------------------------------------------------------------------------


class TestFormatCost:
    def test_sub_cent(self) -> None:
        assert format_cost(0.0042) == "$0.0042"

    def test_above_cent(self) -> None:
        assert format_cost(1.234) == "$1.23"

    def test_zero(self) -> None:
        assert format_cost(0.0) == "$0.0000"

    def test_exactly_one_cent(self) -> None:
        assert format_cost(0.01) == "$0.01"


class TestFormatTokens:
    def test_small(self) -> None:
        assert format_tokens(42) == "42"

    def test_thousands(self) -> None:
        assert format_tokens(1_234_567) == "1,234,567"


class TestFormatCacheRate:
    def test_zero_total(self) -> None:
        assert format_cache_rate(0, 0) == "0.0%"

    def test_half(self) -> None:
        assert format_cache_rate(50, 100) == "50.0%"

    def test_fractional(self) -> None:
        assert format_cache_rate(1, 3) == "33.3%"


# ---------------------------------------------------------------------------
# tracker.py
# ---------------------------------------------------------------------------


def _test_pricing() -> dict[str, ModelPricing]:
    """Simple pricing table for deterministic tests."""
    return {
        "model-a": ModelPricing(input=1e-6, output=2e-6, cached=0.5e-6),
        "model-b": ModelPricing(input=2e-6, output=4e-6, cached=1e-6),
    }


class TestCostTrackerBasics:
    def test_empty_tracker(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        assert tracker.total_cost == 0.0
        assert tracker.total_input_tokens == 0
        assert tracker.total_output_tokens == 0
        assert tracker.total_cached_tokens == 0
        assert tracker.turn_count == 0
        assert tracker.cache_hit_rate == 0.0

    def test_single_turn(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        turn = tracker.record_turn(
            model="model-a",
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=200,
        )
        assert isinstance(turn, TurnUsage)
        assert turn.model == "model-a"
        assert turn.input_tokens == 1000
        assert turn.output_tokens == 500
        assert turn.cached_tokens == 200
        # cost = 1000*1e-6 + 500*2e-6 + 200*0.5e-6 = 0.001 + 0.001 + 0.0001
        expected = 0.0021
        assert turn.cost == pytest.approx(expected)
        assert tracker.total_cost == pytest.approx(expected)
        assert tracker.turn_count == 1

    def test_multiple_turns_accumulate(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        t1 = tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        t2 = tracker.record_turn(model="model-b", input_tokens=200, output_tokens=100)
        assert tracker.total_cost == pytest.approx(t1.cost + t2.cost)
        assert tracker.total_input_tokens == 300
        assert tracker.total_output_tokens == 150
        assert tracker.turn_count == 2

    def test_cached_tokens_default_zero(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        turn = tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        assert turn.cached_tokens == 0


class TestCostTrackerPerModel:
    def test_per_model_breakdown(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        tracker.record_turn(model="model-a", input_tokens=200, output_tokens=100)
        tracker.record_turn(model="model-b", input_tokens=50, output_tokens=25)

        models = tracker.models
        assert len(models) == 2
        assert models["model-a"].turns == 2
        assert models["model-a"].input_tokens == 300
        assert models["model-a"].output_tokens == 150
        assert models["model-b"].turns == 1


class TestCostTrackerCacheRate:
    def test_cache_hit_rate(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        tracker.record_turn(
            model="model-a", input_tokens=800, output_tokens=100, cached_tokens=200
        )
        # cache rate = 200 / (800 + 200) = 0.2
        assert tracker.cache_hit_rate == pytest.approx(0.2)

    def test_cache_hit_rate_zero_input(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        assert tracker.cache_hit_rate == 0.0


class TestCostTrackerUnknownModel:
    def test_unknown_model_zero_cost(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        turn = tracker.record_turn(
            model="unknown/model", input_tokens=1000, output_tokens=500
        )
        assert turn.cost == 0.0
        assert tracker.total_input_tokens == 1000
        assert tracker.total_output_tokens == 500


class TestCostTrackerPricingUpdate:
    def test_set_pricing(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        new_pricing = ModelPricing(input=5e-6, output=10e-6, cached=2.5e-6)
        tracker.set_pricing("model-c", new_pricing)
        turn = tracker.record_turn(model="model-c", input_tokens=1000, output_tokens=500)
        expected = 1000 * 5e-6 + 500 * 10e-6
        assert turn.cost == pytest.approx(expected)


class TestCostTrackerBudget:
    def test_no_budget(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        assert tracker.budget is None
        assert tracker.budget_remaining is None
        assert not tracker.is_budget_warning()

    def test_budget_remaining(self) -> None:
        tracker = CostTracker(budget=1.0, pricing=_test_pricing())
        tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        cost = tracker.total_cost
        assert tracker.budget_remaining == pytest.approx(1.0 - cost)

    def test_budget_warning_at_threshold(self) -> None:
        # model-a: cost per turn with 1000 input, 500 output =
        # 1000*1e-6 + 500*2e-6 = 0.002
        tracker = CostTracker(budget=0.01, pricing=_test_pricing())
        assert not tracker.is_budget_warning()
        # 4 turns = 0.008, which is 80% of 0.01
        for _ in range(4):
            tracker.record_turn(model="model-a", input_tokens=1000, output_tokens=500)
        assert tracker.is_budget_warning()

    def test_budget_exceeded_raises(self) -> None:
        tracker = CostTracker(budget=0.005, pricing=_test_pricing())
        # First turn costs 0.002 -- under budget
        tracker.record_turn(model="model-a", input_tokens=1000, output_tokens=500)
        # Second turn costs 0.002 -- still under budget (0.004)
        tracker.record_turn(model="model-a", input_tokens=1000, output_tokens=500)
        # Third turn costs 0.002 -- total 0.006 > 0.005 budget
        with pytest.raises(BudgetExceededError, match="Budget exceeded"):
            tracker.record_turn(model="model-a", input_tokens=1000, output_tokens=500)

    def test_budget_remaining_floors_at_zero(self) -> None:
        tracker = CostTracker(budget=0.001, pricing=_test_pricing())
        # This will exceed budget and raise, but we catch it
        with pytest.raises(BudgetExceededError):
            tracker.record_turn(model="model-a", input_tokens=1000, output_tokens=500)
        assert tracker.budget_remaining == 0.0

    def test_custom_warning_threshold(self) -> None:
        tracker = CostTracker(
            budget=0.01, warning_threshold=0.5, pricing=_test_pricing()
        )
        # 3 turns = 0.006, which is 60% > 50% threshold
        for _ in range(3):
            tracker.record_turn(model="model-a", input_tokens=1000, output_tokens=500)
        assert tracker.is_budget_warning()


class TestCostTrackerSummary:
    def test_summary_contains_key_info(self) -> None:
        tracker = CostTracker(budget=1.0, pricing=_test_pricing())
        tracker.record_turn(
            model="model-a",
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=200,
        )
        text = tracker.summary()
        assert "Cost Summary" in text
        assert "model-a" in text
        assert "Budget:" in text
        assert "Remaining:" in text
        assert "Cache hit rate:" in text

    def test_summary_no_budget(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        text = tracker.summary()
        assert "Budget:" not in text

    def test_summary_multiple_models(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        tracker.record_turn(model="model-b", input_tokens=100, output_tokens=50)
        text = tracker.summary()
        assert "model-a" in text
        assert "model-b" in text
        assert "Per-Model Breakdown" in text


class TestCostTrackerTurns:
    def test_turns_returns_copy(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        turns = tracker.turns
        turns.clear()
        assert tracker.turn_count == 1

    def test_models_returns_copy(self) -> None:
        tracker = CostTracker(pricing=_test_pricing())
        tracker.record_turn(model="model-a", input_tokens=100, output_tokens=50)
        models = tracker.models
        models.clear()
        assert len(tracker.models) == 1
