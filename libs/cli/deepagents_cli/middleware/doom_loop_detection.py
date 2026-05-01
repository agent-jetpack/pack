"""Detect repeated identical tool calls and inject a redirect message."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)


class DoomLoopDetectionMiddleware(AgentMiddleware):
    """Detect repeated identical tool calls and inject a redirect message.

    Tracks recent tool calls as ``(name, args_hash)`` tuples. When 3+
    consecutive identical calls are detected, a warning is appended telling
    the agent to try a different approach. Follows ForgeCode's doom loop
    detection pattern.
    """

    _THRESHOLD = 3

    def __init__(self) -> None:
        super().__init__()
        self._history: list[tuple[str, int]] = []

    def _signature(self, request: ToolCallRequest) -> tuple[str, int]:
        import hashlib

        name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        args_hash = int(hashlib.md5(str(sorted(args.items())).encode()).hexdigest()[:8], 16)  # noqa: S324
        return (name, args_hash)

    def _is_doom_loop(self) -> int:
        """Return the consecutive repeat count, or 0 if no loop detected."""
        if len(self._history) < self._THRESHOLD:
            return 0
        last = self._history[-1]
        count = 0
        for sig in reversed(self._history):
            if sig == last:
                count += 1
            else:
                break
        return count if count >= self._THRESHOLD else 0

    def _inject_warning(
        self,
        result: ToolMessage | Command[Any],
        count: int,
    ) -> ToolMessage | Command[Any]:
        from langchain_core.messages import ToolMessage as LCToolMessage

        if not isinstance(result, LCToolMessage):
            return result
        warning = (
            f"\n\n⚠️ STUCK: You have made {count} identical tool calls in a row. "
            "You are NOT making progress. STOP and try a completely different "
            "approach. Do NOT retry the same command or arguments."
        )
        return LCToolMessage(
            content=f"{result.content}{warning}" if isinstance(result.content, str) else str(result.content) + warning,
            name=result.name,
            tool_call_id=result.tool_call_id,
            status=getattr(result, "status", None),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        sig = self._signature(request)
        self._history.append(sig)
        result = handler(request)
        count = self._is_doom_loop()
        if count:
            logger.warning("Doom loop detected: %d identical calls to %s", count, sig[0])
            return self._inject_warning(result, count)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        sig = self._signature(request)
        self._history.append(sig)
        result = await handler(request)
        count = self._is_doom_loop()
        if count:
            logger.warning("Doom loop detected: %d identical calls to %s", count, sig[0])
            return self._inject_warning(result, count)
        return result


__all__ = ["DoomLoopDetectionMiddleware"]
