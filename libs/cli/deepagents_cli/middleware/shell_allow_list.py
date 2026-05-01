"""Validate shell commands against an allow-list without HITL interrupts."""

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


class ShellAllowListMiddleware(AgentMiddleware):
    """Validate shell commands against an allow-list without HITL interrupts.

    When the agent invokes a shell tool (any tool in `SHELL_TOOL_NAMES`),
    this middleware checks the command against the configured allow-list
    **before execution**. Rejected commands are returned as error `ToolMessage`
    objects — the graph never pauses, so LangSmith traces stay as a single
    continuous run.

    Use this middleware in non-interactive mode to avoid the
    interrupt/resume cycle that fragments traces.
    """

    def __init__(self, allow_list: list[str]) -> None:
        """Initialize with the shell allow-list to validate commands against.

        Args:
            allow_list: Allowed command names (e.g. `["ls", "cat", "grep"]`).
                Must be a non-empty restrictive list — not `SHELL_ALLOW_ALL`.

        Raises:
            ValueError: If `allow_list` is empty.
            TypeError: If `allow_list` is the `SHELL_ALLOW_ALL` sentinel.
        """
        from deepagents_cli.config import SHELL_ALLOW_ALL

        super().__init__()
        if not allow_list:
            msg = "allow_list must not be empty; disable shell access instead"
            raise ValueError(msg)
        if isinstance(allow_list, type(SHELL_ALLOW_ALL)):
            msg = (
                "SHELL_ALLOW_ALL should not be used with "
                "ShellAllowListMiddleware; use auto_approve=True instead"
            )
            raise TypeError(msg)
        self._allow_list = list(allow_list)

    def _validate_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        """Return an error tool message when a shell command is not allowed."""
        from langchain_core.messages import ToolMessage as LCToolMessage

        from deepagents_cli.config import SHELL_TOOL_NAMES, is_shell_command_allowed

        tool_name = request.tool_call["name"]
        if tool_name not in SHELL_TOOL_NAMES:
            return None

        args = request.tool_call.get("args") or {}
        command = args.get("command", "")
        if is_shell_command_allowed(command, self._allow_list):
            logger.debug("Shell command allowed: %r", command)
            return None

        logger.warning("Shell command rejected by allow-list: %r", command)
        allowed_str = ", ".join(self._allow_list)
        return LCToolMessage(
            content=(
                f"Shell command rejected: `{command}` is not in the allow-list. "
                f"Allowed commands: {allowed_str}. "
                f"Please use an allowed command or try another approach."
            ),
            name=tool_name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if (rejection := self._validate_tool_call(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if (rejection := self._validate_tool_call(request)) is not None:
            return rejection
        return await handler(request)


__all__ = ["ShellAllowListMiddleware"]
