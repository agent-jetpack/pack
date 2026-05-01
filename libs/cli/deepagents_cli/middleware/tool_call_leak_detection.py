"""Detect raw tool call syntax leaked into AI message text."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class ToolCallLeakDetectionMiddleware(AgentMiddleware):
    """Detect raw tool call syntax leaked into AI message text.

    Some models (notably DeepSeek) occasionally emit their internal tool-call
    markup as literal text instead of structured tool invocations. This
    middleware scans AI message content for known leak patterns and strips them,
    logging a warning so operators can track the issue.
    """

    _LEAK_PATTERNS = (
        "<｜tool▁calls▁begin｜>",
        "<｜tool▁call▁begin｜>",
        "<｜tool▁sep｜>",
        "<｜tool▁call▁end｜>",
        "<｜tool▁calls▁end｜>",
    )

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove content inside code fences to avoid false positives."""
        import re

        return re.sub(r"```.*?```", "", text, flags=re.DOTALL)

    def _contains_leak(self, text: str) -> bool:
        stripped = self._strip_code_fences(text)
        return any(pattern in stripped for pattern in self._LEAK_PATTERNS)

    def _clean_leaked_text(self, text: str) -> str:
        import re

        for pattern in self._LEAK_PATTERNS:
            text = text.replace(pattern, "")
        text = re.sub(
            r"function<｜tool▁sep｜>.*?(?=<｜|$)",
            "",
            text,
            flags=re.DOTALL,
        )
        return text.strip()

    def wrap_model_call(
        self,
        messages: list,
        handler: Callable[[list], Any],
    ) -> Any:
        result = handler(messages)
        if hasattr(result, "content") and isinstance(result.content, str):
            if self._contains_leak(result.content):
                logger.warning(
                    "Tool call syntax leak detected in AI response — "
                    "stripping leaked content (model: %s)",
                    getattr(result, "response_metadata", {}).get("model", "unknown"),
                )
                result.content = self._clean_leaked_text(result.content)
        return result

    async def awrap_model_call(
        self,
        messages: list,
        handler: Callable[[list], Awaitable[Any]],
    ) -> Any:
        result = await handler(messages)
        if hasattr(result, "content") and isinstance(result.content, str):
            if self._contains_leak(result.content):
                logger.warning(
                    "Tool call syntax leak detected in AI response — "
                    "stripping leaked content (model: %s)",
                    getattr(result, "response_metadata", {}).get("model", "unknown"),
                )
                result.content = self._clean_leaked_text(result.content)
        return result


__all__ = ["ToolCallLeakDetectionMiddleware"]
