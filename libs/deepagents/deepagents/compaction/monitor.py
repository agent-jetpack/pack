"""Token monitor that triggers compaction at configurable thresholds.

Checks context size before each model call and selects the appropriate
compaction tier based on how close we are to the context window limit.
"""

from __future__ import annotations

from enum import Enum, auto

from langchain_core.messages import AnyMessage
from langchain_core.messages.utils import count_tokens_approximately


class CompactionTier(Enum):
    """Compaction aggressiveness levels."""

    NONE = auto()     # Under threshold — no action needed
    TRIM = auto()     # 70-80% — remove old tool results
    COLLAPSE = auto() # 80-90% — replace verbose results with summaries
    SUMMARIZE = auto() # 90%+ — full 9-segment summarization


class CompactionMonitor:
    """Monitor token usage and determine when compaction is needed.

    Args:
        context_window: Maximum tokens the model supports.
        trim_threshold: Fraction of context window that triggers Tier 1 trim.
        collapse_threshold: Fraction that triggers Tier 2 collapse.
        summarize_threshold: Fraction that triggers Tier 3 summarization.
    """

    def __init__(
        self,
        context_window: int = 200_000,
        *,
        trim_threshold: float = 0.70,
        collapse_threshold: float = 0.80,
        summarize_threshold: float = 0.90,
    ) -> None:
        self._context_window = context_window
        self._trim_threshold = trim_threshold
        self._collapse_threshold = collapse_threshold
        self._summarize_threshold = summarize_threshold

    @property
    def context_window(self) -> int:
        """Maximum tokens the model supports."""
        return self._context_window

    def check(self, messages: list[AnyMessage], system_tokens: int = 0) -> CompactionTier:
        """Check if compaction is needed based on current token count.

        Args:
            messages: Current conversation messages.
            system_tokens: Estimated tokens used by system prompt.

        Returns:
            The compaction tier to apply.
        """
        token_count = count_tokens_approximately(messages) + system_tokens
        ratio = token_count / self._context_window

        if ratio >= self._summarize_threshold:
            return CompactionTier.SUMMARIZE
        if ratio >= self._collapse_threshold:
            return CompactionTier.COLLAPSE
        if ratio >= self._trim_threshold:
            return CompactionTier.TRIM
        return CompactionTier.NONE

    def token_count(self, messages: list[AnyMessage]) -> int:
        """Count tokens in a message list.

        Args:
            messages: Messages to count.

        Returns:
            Approximate token count.
        """
        return count_tokens_approximately(messages)
