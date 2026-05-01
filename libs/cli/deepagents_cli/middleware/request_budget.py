"""Track model call count and inject budget-awareness prompts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class RequestBudgetMiddleware(AgentMiddleware):
    """Track model call count and inject budget-awareness prompts.

    Injects system reminders at 50%, 75%, and 90% of the request budget
    so the agent can prioritize and wrap up gracefully. Follows ForgeCode's
    ``max_requests_per_turn: 100`` pattern.
    """

    def __init__(self, max_requests: int = 100) -> None:
        super().__init__()
        self._max = max_requests
        self._count = 0
        self._notified: set[int] = set()

    def _log_budget(self) -> None:
        """Log budget status at threshold crossings."""
        self._count += 1
        pct = int(self._count / self._max * 100)

        for threshold in (50, 75, 90):
            if pct >= threshold and threshold not in self._notified:
                self._notified.add(threshold)
                level = "warning" if threshold < 90 else "error"
                getattr(logger, level)(
                    "Request budget: %d/%d (%d%%) used",
                    self._count,
                    self._max,
                    pct,
                )
                break

    def wrap_model_call(self, messages: list, handler: Callable) -> Any:
        self._log_budget()
        return handler(messages)

    async def awrap_model_call(self, messages: list, handler: Callable) -> Any:
        self._log_budget()
        return await handler(messages)


__all__ = ["RequestBudgetMiddleware"]
