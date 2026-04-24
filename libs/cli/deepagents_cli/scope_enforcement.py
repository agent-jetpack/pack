"""Scope enforcement — gates file writes against a ``TaskPolicy``.

Intercepts ``write_file`` and ``edit_file`` calls at the
``wrap_tool_call`` hook and rejects any whose target path sits outside
the policy's ``allowed_paths`` globs. Also enforces ``max_files_changed``
by tracking distinct target paths across the run.

The rejection payload is a **teach-at-failure** message: it names the
violated rule, points at the allowed paths, and suggests what to do
instead. The goal is that the agent can recover on the next turn
without escalating to a human.

Phase A.2 of the agent-harness roadmap. Consumes ``TaskPolicy`` from
``policy.py``.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.tools.tool_node import ToolCallRequest
    from langgraph.types import Command

    from deepagents_cli.policy import TaskPolicy

logger = logging.getLogger(__name__)


# Tool names that write to the filesystem. Keep this narrow — the
# middleware intentionally doesn't gate `execute` (shell) because a
# shell-level scope check requires a shell-allow-list, which is a
# separate middleware's job.
_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})


def _path_from_args(tool_call: dict[str, Any]) -> str | None:
    """Extract the target file path from a write-tool call.

    Both ``write_file`` and ``edit_file`` in deepagents accept either
    ``path`` or ``file_path`` depending on version. We check both so the
    middleware survives schema drift.
    """
    args = tool_call.get("args") or {}
    path = args.get("path") or args.get("file_path")
    if isinstance(path, str) and path.strip():
        return path
    return None


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    """True when ``path`` matches at least one glob in ``patterns``.

    Two glob quirks we handle here:

    1. Policy globs are written from a repo-root perspective, but agent
       paths are absolute container paths (``/app/docs/foo.md``). We
       iterate every path suffix so ``docs/**`` matches ``/app/docs/x``
       without hardcoding a workdir.
    2. ``fnmatch`` does not treat ``**/`` as "zero or more levels". We
       expand each pattern to also try the ``**/`` stripped form, so
       ``**/*.md`` matches top-level ``foo.md``.
    """
    segments = [s for s in path.split("/") if s]
    candidates = {path, path.lstrip("/")}
    for i in range(len(segments)):
        candidates.add("/".join(segments[i:]))

    expanded_patterns: list[str] = []
    for pattern in patterns:
        expanded_patterns.append(pattern)
        if pattern.startswith("**/"):
            expanded_patterns.append(pattern[3:])

    for pattern in expanded_patterns:
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, pattern):
                return True
    return False


class ScopeEnforcementMiddleware(AgentMiddleware):
    """Reject writes outside policy-allowed paths or beyond file-change cap.

    The middleware is idempotent: the first rejection records the
    path into the ``touched_paths`` set but does NOT count toward
    ``max_files_changed``. Only successful writes increment the count.
    That way a single typo doesn't count against the agent's file budget.

    Args:
        policy: The active ``TaskPolicy``. When None, the middleware
            no-ops (so non-harness callers keep working).
        violation_recorder: Optional callable invoked on each rejection
            with ``(tool_name, path, reason)``. Phase A.3 wires this to
            the ratchet; until then it's unused.
        disabled: Hard kill-switch for tests and emergency debugging.
    """

    def __init__(
        self,
        *,
        policy: TaskPolicy | None = None,
        violation_recorder: Callable[[str, str, str], None] | None = None,
        disabled: bool = False,
    ) -> None:
        self.policy = policy
        self.violation_recorder = violation_recorder
        self.disabled = disabled
        # Set of distinct paths successfully written to this run.
        self._touched_paths: set[str] = set()

    def _active(self) -> bool:
        return (not self.disabled) and (self.policy is not None)

    def _check_write(
        self,
        request: ToolCallRequest,
    ) -> ToolMessage | None:
        """Return a rejection ToolMessage when this write isn't allowed.

        Returns ``None`` when the call should proceed.
        """
        if not self._active():
            return None
        assert self.policy is not None  # narrowed by _active

        tool_name = request.tool_call.get("name", "")
        if tool_name not in _WRITE_TOOL_NAMES:
            return None

        path = _path_from_args(request.tool_call)
        if path is None:
            # Let the tool itself reject malformed args.
            return None

        # Check 1: path must match an allowed glob.
        if not _matches_any(path, self.policy.allowed_paths):
            reason = "out-of-scope path"
            if self.violation_recorder:
                self.violation_recorder(tool_name, path, reason)
            return _reject(
                tool_call=request.tool_call,
                path=path,
                reason=reason,
                policy=self.policy,
            )

        # Check 2: file-count cap. Only applies to files not yet touched.
        new_touch = path not in self._touched_paths
        if new_touch and len(self._touched_paths) >= self.policy.max_files_changed:
            reason = "max_files_changed exceeded"
            if self.violation_recorder:
                self.violation_recorder(tool_name, path, reason)
            return _reject(
                tool_call=request.tool_call,
                path=path,
                reason=reason,
                policy=self.policy,
            )

        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Gate writes before execution; pass everything else through."""
        rejection = self._check_write(request)
        if rejection is not None:
            logger.warning(
                "ScopeEnforcement: rejected %s on %s (reason=%s)",
                request.tool_call.get("name"),
                _path_from_args(request.tool_call),
                rejection.status,
            )
            return rejection

        result = handler(request)

        # Record touch on successful writes only.
        path = _path_from_args(request.tool_call)
        tool_name = request.tool_call.get("name", "")
        if (
            path is not None
            and tool_name in _WRITE_TOOL_NAMES
            and isinstance(result, ToolMessage)
            and result.status != "error"
        ):
            self._touched_paths.add(path)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async version."""
        rejection = self._check_write(request)
        if rejection is not None:
            logger.warning(
                "ScopeEnforcement: rejected %s on %s (reason=%s)",
                request.tool_call.get("name"),
                _path_from_args(request.tool_call),
                rejection.status,
            )
            return rejection

        result = await handler(request)

        path = _path_from_args(request.tool_call)
        tool_name = request.tool_call.get("name", "")
        if (
            path is not None
            and tool_name in _WRITE_TOOL_NAMES
            and isinstance(result, ToolMessage)
            and result.status != "error"
        ):
            self._touched_paths.add(path)
        return result


def _reject(
    *,
    tool_call: dict[str, Any],
    path: str,
    reason: str,
    policy: TaskPolicy,
) -> ToolMessage:
    """Build a teach-at-failure rejection message.

    Mentions: what was rejected, why, what's allowed, and what to do
    next. Keeping the suggestion concrete is the whole point — a vague
    rejection just burns a tool call.
    """
    allowed = ", ".join(policy.allowed_paths) or "(none — no writes allowed)"
    if reason == "max_files_changed exceeded":
        hint = (
            f"Current task policy '{policy.task_type}' caps writes at "
            f"{policy.max_files_changed} distinct files. Finish and "
            "verify what's already written before touching new files, "
            "or escalate if the scope genuinely needs to grow."
        )
    else:
        hint = (
            f"Current task policy '{policy.task_type}' only allows "
            f"writes to: {allowed}. Either pick a path matching one of "
            "those globs, or stop and explain why this file needs to be "
            "touched — the policy can be overridden by the user but not "
            "by the agent."
        )

    content = (
        f"⛔️ Scope violation: write to `{path}` rejected "
        f"({reason}).\n\n{hint}"
    )
    return ToolMessage(
        content=content,
        name=tool_call.get("name", "write_file"),
        tool_call_id=tool_call["id"],
        status="error",
    )


__all__ = ["ScopeEnforcementMiddleware"]
