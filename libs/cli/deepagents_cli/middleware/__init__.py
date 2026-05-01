"""Agent middleware classes extracted from ``agent.py`` for easier maintenance.

Each module here owns a single LangGraph ``AgentMiddleware`` subclass.
``agent.py`` re-exports the same names so existing imports keep working:

    from deepagents_cli.agent import ShellAllowListMiddleware  # still works
    from deepagents_cli.middleware import ShellAllowListMiddleware  # preferred
"""

from __future__ import annotations

from deepagents_cli.middleware.doom_loop_detection import DoomLoopDetectionMiddleware
from deepagents_cli.middleware.edit_verification import EditVerificationMiddleware
from deepagents_cli.middleware.error_reflection import ErrorReflectionMiddleware
from deepagents_cli.middleware.python_syntax_check import PythonSyntaxCheckMiddleware
from deepagents_cli.middleware.read_before_write import ReadBeforeWriteMiddleware
from deepagents_cli.middleware.request_budget import RequestBudgetMiddleware
from deepagents_cli.middleware.shell_allow_list import ShellAllowListMiddleware
from deepagents_cli.middleware.tool_call_leak_detection import (
    ToolCallLeakDetectionMiddleware,
)

__all__ = [
    "DoomLoopDetectionMiddleware",
    "EditVerificationMiddleware",
    "ErrorReflectionMiddleware",
    "PythonSyntaxCheckMiddleware",
    "ReadBeforeWriteMiddleware",
    "RequestBudgetMiddleware",
    "ShellAllowListMiddleware",
    "ToolCallLeakDetectionMiddleware",
]
