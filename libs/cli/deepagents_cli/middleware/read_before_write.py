"""Enforce read-before-edit at the tool level."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command


class ReadBeforeWriteMiddleware(AgentMiddleware):
    """Enforce read-before-edit at the tool level.

    Tracks which files have been read in the current session. Blocks
    ``edit_file`` calls on files that haven't been read first, returning
    an actionable error. ``write_file`` (full overwrites) is allowed
    without prior read since the agent is creating the entire content.
    """

    _READ_TOOLS = frozenset({"read_file", "read"})
    _EDIT_TOOLS = frozenset({"edit_file", "edit", "patch"})

    def __init__(self) -> None:
        super().__init__()
        self._read_files: set[str] = set()

    def _check(self, request: ToolCallRequest) -> ToolMessage | None:
        from langchain_core.messages import ToolMessage as LCToolMessage

        name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}

        if name in self._READ_TOOLS:
            path = args.get("file_path", "") or args.get("path", "")
            if path:
                self._read_files.add(path)
            return None

        if name in self._EDIT_TOOLS:
            path = args.get("file_path", "") or args.get("path", "")
            if path and path not in self._read_files:
                return LCToolMessage(
                    content=(
                        f"⚠️ You must read '{path}' before editing it. "
                        "Use read_file first to understand the current content, "
                        "then retry your edit."
                    ),
                    name=name,
                    tool_call_id=request.tool_call.get("id", ""),
                    status="error",
                )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if (rejection := self._check(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if (rejection := self._check(request)) is not None:
            return rejection
        return await handler(request)


__all__ = ["ReadBeforeWriteMiddleware"]
