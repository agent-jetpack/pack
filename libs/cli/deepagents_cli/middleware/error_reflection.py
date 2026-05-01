"""Force the agent to reflect on tool failures before retrying."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command


class ErrorReflectionMiddleware(AgentMiddleware):
    """Force the agent to reflect on tool failures before retrying.

    When a tool call returns an error, appends a reflection prompt with
    remaining attempt count (ForgeCode pattern). After ``max_failures``
    errors in a turn, the reflection escalates to "change strategy."
    """

    _SHELL_TOOL_NAMES = frozenset({"execute", "shell"})
    _MAX_FAILURES = 5
    _ERROR_INDICATORS = (
        "error:",
        "Error:",
        "ERROR:",
        "Traceback",
        "FAILED",
        "command not found",
        "No such file",
        "Permission denied",
    )

    def __init__(self) -> None:
        super().__init__()
        self._failure_count = 0

    def _needs_reflection(
        self,
        result: ToolMessage | Command[Any],
        tool_name: str,
    ) -> bool:
        from langchain_core.messages import ToolMessage as LCToolMessage

        if not isinstance(result, LCToolMessage):
            return False
        if getattr(result, "status", None) == "error":
            return True
        content = result.content if isinstance(result.content, str) else str(result.content)
        if tool_name in self._SHELL_TOOL_NAMES:
            if any(ind in content for ind in self._ERROR_INDICATORS):
                return True
        return False

    def _inject_reflection(
        self,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        from langchain_core.messages import ToolMessage as LCToolMessage

        if not isinstance(result, LCToolMessage):
            return result

        self._failure_count += 1
        remaining = max(0, self._MAX_FAILURES - self._failure_count)

        if remaining == 0:
            escalation = (
                "\n\n🛑 ERROR BUDGET EXHAUSTED. You have failed {n} tool calls this session. "
                "You MUST change your fundamental approach. Do NOT continue with the same strategy."
            ).format(n=self._failure_count)
        else:
            escalation = (
                "\n\n⚠️ TOOL FAILED ({n}/{max} failures, {r} remaining). "
                "Before retrying, you MUST reflect:\n"
                "1. What exactly went wrong with this tool call?\n"
                "2. Why did it fail — wrong tool, wrong arguments, or wrong approach?\n"
                "3. What specific change will you make before retrying?\n"
                "Do NOT retry the same command without changes."
            ).format(n=self._failure_count, max=self._MAX_FAILURES, r=remaining)

        return LCToolMessage(
            content=f"{result.content}{escalation}" if isinstance(result.content, str) else str(result.content) + escalation,
            name=result.name,
            tool_call_id=result.tool_call_id,
            status=getattr(result, "status", None),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        result = handler(request)
        if self._needs_reflection(result, tool_name):
            return self._inject_reflection(result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        result = await handler(request)
        if self._needs_reflection(result, tool_name):
            return self._inject_reflection(result)
        return result


__all__ = ["ErrorReflectionMiddleware"]
