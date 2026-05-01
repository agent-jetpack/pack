"""Run ``ast.parse()`` after writing ``.py`` files to catch syntax errors early."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command


class PythonSyntaxCheckMiddleware(AgentMiddleware):
    """Run ``ast.parse()`` after writing ``.py`` files to catch syntax errors early.

    Only active in non-interactive mode to avoid slowing interactive workflows.
    When a syntax error is found, a warning is appended to the tool result so
    the agent can fix the file before moving on.
    """

    _WRITE_TOOL_NAMES = frozenset({"write_file"})

    def _check_syntax(
        self,
        result: ToolMessage | Command[Any],
        tool_name: str,
        args: dict,
    ) -> ToolMessage | Command[Any]:
        from langchain_core.messages import ToolMessage as LCToolMessage

        if tool_name not in self._WRITE_TOOL_NAMES:
            return result
        if not isinstance(result, LCToolMessage):
            return result

        file_path = args.get("file_path", "") or args.get("path", "")
        if not file_path.endswith(".py"):
            return result

        content = args.get("content", "")
        if not content:
            return result

        import ast

        try:
            ast.parse(content, filename=file_path)
        except SyntaxError as e:
            warning = (
                f"\n\n⚠️ SYNTAX ERROR in {file_path} at line {e.lineno}: "
                f"{e.msg}. The file was written but contains invalid Python. "
                "Fix the syntax before proceeding."
            )
            return LCToolMessage(
                content=f"{result.content}{warning}" if isinstance(result.content, str) else str(result.content) + warning,
                name=result.name,
                tool_call_id=result.tool_call_id,
            )
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        result = handler(request)
        return self._check_syntax(result, tool_name, args)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        result = await handler(request)
        return self._check_syntax(result, tool_name, args)


__all__ = ["PythonSyntaxCheckMiddleware"]
