"""Parallel tool execution middleware -- exposes ParallelToolExecutor via PackState.

Full parallel execution requires intercepting MULTIPLE tool calls at once,
which is not supported by the single-call awrap_tool_call middleware pattern.
This module provides a lightweight wrapper that stores the executor in PackState
for use by callers that can batch tool calls (e.g., a custom LangGraph tool
execution node).

The executor itself handles dependency-aware scheduling: independent tool calls
run concurrently while dependent chains (same file path, at least one write)
run sequentially.
"""

from __future__ import annotations

from deepagents.execution.parallel import ParallelToolExecutor

__all__ = ["ParallelToolExecutor"]
