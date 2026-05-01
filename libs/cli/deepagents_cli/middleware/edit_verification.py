"""Surface edit_file failures explicitly so the agent knows the edit didn't apply."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command


class EditVerificationMiddleware(AgentMiddleware):
    """Surface edit_file failures explicitly so the agent knows the edit didn't apply.

    When ``edit_file`` returns a result indicating the ``old_string`` was not
    found, this middleware appends a clear warning to the tool message so the
    agent can re-read the file and retry with corrected context.
    """

    _EDIT_TOOL_NAMES = frozenset({"edit_file"})
    _FAILURE_INDICATORS = (
        "old_string was not found",
        "No match found",
        "not found in file",
        "no changes were made",
    )

    def _check_result(
        self,
        result: ToolMessage | Command[Any],
        tool_name: str,
    ) -> ToolMessage | Command[Any]:
        from langchain_core.messages import ToolMessage as LCToolMessage

        if tool_name not in self._EDIT_TOOL_NAMES:
            return result
        if not isinstance(result, LCToolMessage):
            return result
        content = result.content if isinstance(result.content, str) else str(result.content)
        if any(indicator in content.lower() for indicator in (i.lower() for i in self._FAILURE_INDICATORS)):
            return LCToolMessage(
                content=(
                    f"{content}\n\n⚠️ EDIT FAILED: The old_string was not found "
                    "in the file. The file was NOT modified. Re-read the file to "
                    "see its actual content before retrying."
                ),
                name=result.name,
                tool_call_id=result.tool_call_id,
                status="error",
            )
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        result = handler(request)
        return self._check_result(result, tool_name)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        result = await handler(request)
        return self._check_result(result, tool_name)


__all__ = ["EditVerificationMiddleware"]
