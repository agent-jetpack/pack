"""Cost and token formatting utilities for human-readable display."""

from __future__ import annotations


def format_cost(cost: float) -> str:
    """Format a USD cost value for display.

    Shows sub-cent values with more precision to avoid misleading "$0.00"
    for cheap models during early turns.

    Args:
        cost: Cost in USD.

    Returns:
        Formatted cost string like "$0.0042" or "$1.23".
    """
    _one_cent = 0.01
    if cost < _one_cent:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def format_tokens(count: int) -> str:
    """Format a token count with thousands separators for readability.

    Args:
        count: Number of tokens.

    Returns:
        Formatted string like "1,234" or "1,234,567".
    """
    return f"{count:,}"


def format_cache_rate(hits: int, total: int) -> str:
    """Format a cache hit rate as a percentage.

    Args:
        hits: Number of cached tokens.
        total: Total number of input tokens (including cached).

    Returns:
        Formatted percentage string like "45.2%" or "0.0%" when total is zero.
    """
    if total == 0:
        return "0.0%"
    rate = (hits / total) * 100
    return f"{rate:.1f}%"
