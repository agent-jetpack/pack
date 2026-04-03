"""Hook engine that dispatches lifecycle events to external commands.

The engine loads hook definitions at construction time and fires matching
hooks when events occur. Each hook is an async subprocess with timeout
enforcement, template variable substitution, and optional output injection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from deepagents.hooks.events import HookEvent  # noqa: TC001 — used at runtime by dataclass and fire()
from deepagents.hooks.runners import HookResult, run_hook

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookDefinition:
    """Configuration for a single hook.

    Args:
        event: The lifecycle event that triggers this hook.
        command: Shell command to execute. May contain template variables
            like ``{tool_name}`` or ``{file_path}``.
        tool_filter: When set, the hook only fires for the named tool.
        inject_output: When true, the hook's stdout is returned for
            injection into model context.
        blocking: When true, a non-zero exit code signals that the
            triggering action should be prevented.
        timeout: Maximum seconds before the hook subprocess is killed.
    """

    event: HookEvent
    command: str
    tool_filter: str | None = None
    inject_output: bool = False
    blocking: bool = False
    timeout: int = 10


class HookEngine:
    """Dispatches lifecycle events to configured hook commands.

    Hooks are grouped by event at construction time for efficient lookup.
    The `fire` method runs all matching hooks in order and returns their
    results.

    Args:
        hooks: List of hook definitions to register.
    """

    def __init__(self, hooks: list[HookDefinition] | None = None) -> None:
        """Initialize the engine with a list of hook definitions.

        Args:
            hooks: Hook definitions to register. Defaults to empty.
        """
        self._hooks: list[HookDefinition] = list(hooks) if hooks else []
        self._by_event: dict[HookEvent, list[HookDefinition]] = {}
        for hook in self._hooks:
            self._by_event.setdefault(hook.event, []).append(hook)

    async def fire(
        self,
        event: HookEvent,
        context: dict[str, str] | None = None,
    ) -> list[HookResult]:
        """Execute all hooks matching the event and context.

        Hooks for the event are executed sequentially in registration
        order. If a hook has a ``tool_filter``, it only fires when the
        context's ``tool_name`` matches.

        Args:
            event: The lifecycle event being fired.
            context: Template variable values (``tool_name``,
                ``file_path``, ``command``, ``args``, ``result``).

        Returns:
            List of `HookResult` objects, one per executed hook, in
            execution order.
        """
        ctx = context or {}
        candidates = self._by_event.get(event, [])
        results: list[HookResult] = []

        for hook in candidates:
            if hook.tool_filter and ctx.get("tool_name") != hook.tool_filter:
                continue

            result = await run_hook(
                command=hook.command,
                context=ctx,
                timeout=hook.timeout,
                inject_output=hook.inject_output,
            )
            results.append(result)

            if hook.blocking and result.return_code != 0:
                logger.warning(
                    "Blocking hook failed (rc=%d) for event %s: %s",
                    result.return_code,
                    event.value,
                    hook.command,
                )
                break

        return results
