"""Parallel tool execution with automatic dependency detection.

Independent tool calls (no shared file paths) run concurrently via
`asyncio.gather`. Dependent calls (shared file paths in arguments)
run sequentially to preserve ordering guarantees.
"""

from deepagents.execution.parallel import ParallelToolExecutor, ToolCall, ToolResult

__all__ = [
    "ParallelToolExecutor",
    "ToolCall",
    "ToolResult",
]
