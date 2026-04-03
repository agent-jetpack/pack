"""Parallel tool executor with dependency-aware scheduling.

Classifies tool calls by file-path overlap and executes independent
groups concurrently while keeping dependent chains sequential.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


# Heuristic: match anything that looks like a file path in tool arguments.
_PATH_PATTERN = re.compile(
    r"""(?:^|[\s"'=:,])"""  # preceded by whitespace or common delimiters
    r"""((?:/|\.\.?/)[\w./\-]+)""",  # a slash-prefixed or relative path
)

_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "ls",
        "glob",
        "grep",
        "web_search",
        "fetch_url",
    }
)


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation to be scheduled.

    Args:
        name: Tool name (e.g. `read_file`, `write_file`).
        args: Keyword arguments forwarded to the callable.
        fn: Async callable that performs the tool action.
    """

    name: str
    args: dict[str, Any]
    fn: Callable[..., Coroutine[Any, Any, Any]]


@dataclass
class ToolResult:
    """Result of a single tool invocation.

    Args:
        index: Original position in the submitted call list.
        name: Tool name that was invoked.
        value: Return value on success, `None` on failure.
        error: Exception instance if the call failed.
    """

    index: int
    name: str
    value: Any = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        """Return `True` when the call succeeded."""
        return self.error is None


@dataclass
class ParallelToolExecutor:
    """Execute tool calls concurrently when safe, sequentially otherwise.

    Dependency detection is based on file-path overlap in arguments:
    if two calls reference the same path *and* at least one is a write,
    they are considered dependent and run in submission order.

    Args:
        enabled: When `False`, all calls run sequentially.
    """

    enabled: bool = True
    _path_pattern: re.Pattern[str] = field(default=_PATH_PATTERN, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, calls: list[ToolCall]) -> list[ToolResult]:
        """Run *calls* and return results in original submission order.

        Args:
            calls: Ordered list of tool calls to execute.

        Returns:
            List of `ToolResult` objects matching the input order.
        """
        if not calls:
            return []

        if not self.enabled:
            return await self._run_sequential(calls)

        groups = self._build_groups(calls)
        results: dict[int, ToolResult] = {}

        for group in groups:
            if len(group) == 1:
                idx, call = group[0]
                results[idx] = await self._invoke(idx, call)
            else:
                coros = [self._invoke(idx, call) for idx, call in group]
                for result in await asyncio.gather(*coros):
                    results[result.index] = result

        return [results[i] for i in range(len(calls))]

    @classmethod
    def is_concurrency_safe(cls, tool_name: str) -> bool:
        """Return `True` if *tool_name* is a read-only tool safe for parallel use.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            `True` for tools that only read data and have no side effects.
        """
        return tool_name in _READ_ONLY_TOOLS

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_paths(self, args: dict[str, Any]) -> set[str]:
        """Pull file-path-like strings out of tool arguments.

        Args:
            args: Tool keyword arguments.

        Returns:
            Set of normalized path strings found in the argument values.
        """
        paths: set[str] = set()
        for value in args.values():
            if isinstance(value, str):
                for match in self._path_pattern.finditer(value):
                    paths.add(match.group(1))
        return paths

    def _build_groups(
        self, calls: list[ToolCall]
    ) -> list[list[tuple[int, ToolCall]]]:
        """Partition *calls* into sequential groups of independent batches.

        Two calls are *dependent* when they share a file path and at
        least one of them is not a read-only tool.  Dependent calls are
        placed into the same sequential chain; independent calls are
        grouped together for concurrent execution.

        Args:
            calls: Ordered tool calls.

        Returns:
            List of groups.  Each group is a list of `(index, call)`
            tuples that may run concurrently with each other but must
            run after the previous group completes.
        """
        indexed: list[tuple[int, ToolCall, set[str]]] = [
            (i, call, self._extract_paths(call.args)) for i, call in enumerate(calls)
        ]

        groups: list[list[tuple[int, ToolCall]]] = []
        # Track which paths have been "touched" by a write in the
        # current accumulated set of prior groups.
        committed_write_paths: set[str] = set()
        current_group: list[tuple[int, ToolCall]] = []
        current_group_paths: set[str] = set()

        for idx, call, paths in indexed:
            safe = self.is_concurrency_safe(call.name)
            conflicts_with_prior_write = bool(paths & committed_write_paths)
            conflicts_with_current = (
                bool(paths & current_group_paths) and not safe
            )

            if (
                conflicts_with_prior_write or conflicts_with_current
            ) and current_group:
                    groups.append(current_group)
                    # All paths from flushed group are now committed.
                    committed_write_paths |= current_group_paths
                    current_group = []
                    current_group_paths = set()

            current_group.append((idx, call))
            if not safe:
                current_group_paths |= paths

        if current_group:
            groups.append(current_group)

        return groups

    async def _run_sequential(self, calls: list[ToolCall]) -> list[ToolResult]:
        """Execute every call one at a time, in order.

        Args:
            calls: Tool calls to run.

        Returns:
            Results in submission order.
        """
        results: list[ToolResult] = []
        for i, call in enumerate(calls):
            results.append(await self._invoke(i, call))
        return results

    @staticmethod
    async def _invoke(index: int, call: ToolCall) -> ToolResult:
        """Run a single tool call and capture the outcome.

        Args:
            index: Original position in the call list.
            call: The tool call to execute.

        Returns:
            A `ToolResult` with either a value or an error.
        """
        try:
            value = await call.fn(**call.args)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(index=index, name=call.name, error=exc)
        return ToolResult(index=index, name=call.name, value=value)
