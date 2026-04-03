"""Rich hook system for lifecycle event dispatch.

External shell commands can be triggered at key lifecycle points such as
tool calls, model calls, session boundaries, and file modifications.
Hooks run as async subprocesses with timeout enforcement, template
variable substitution, and optional output injection into model context.
"""

from deepagents.hooks.engine import HookDefinition, HookEngine
from deepagents.hooks.events import HookEvent
from deepagents.hooks.runners import HookResult, run_hook, substitute_template

__all__ = [
    "HookDefinition",
    "HookEngine",
    "HookEvent",
    "HookResult",
    "run_hook",
    "substitute_template",
]
