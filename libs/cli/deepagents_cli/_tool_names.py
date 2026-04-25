"""Canonical tool-name sets and path helpers shared by the harness layer.

Several middleware files used to maintain their own ``_WRITE_TOOL_NAMES``
or ``_EDIT_TOOL_NAMES`` constants and their own path-extraction helpers.
The set drifted: scope enforcement counted ``edit_file`` as a write,
loop detection counted both as edits, the architecture middleware in
the SDK added ``str_replace_editor`` and ``apply_patch``, etc. Adding
a new file-mutating tool meant touching every site.

This module is the canonical home. Import the constants and helpers
from here instead of redefining them per-file.
"""

from __future__ import annotations

from typing import Any


# Tools that produce *new* file content. ``write_file`` is the obvious
# one; ``edit_file`` is included because it also writes (just with a
# diff-shaped input). Middleware that gates "any file write" should
# use ``WRITE_TOOLS``.
WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})

# Tools that *modify* existing file content via diff replacement.
# ``EDIT_TOOLS`` is a subset of ``WRITE_TOOLS`` plus diff-style
# variants some backends expose (``str_replace_editor``, ``apply_patch``).
# Middleware that specifically cares about edits-vs-fresh-writes
# should use this set.
EDIT_TOOLS: frozenset[str] = frozenset(
    {"edit_file", "str_replace_editor", "apply_patch"}
)

# Convenience: every tool name considered a file mutation. Same as
# ``WRITE_TOOLS | EDIT_TOOLS`` but pre-computed so callers don't pay
# the union cost on every tool call.
FILE_MUTATING_TOOLS: frozenset[str] = WRITE_TOOLS | EDIT_TOOLS


# Argument keys that may carry the target file path. Schema drift
# between deepagents and tool variants means we have to check both
# common spellings.
_PATH_ARG_KEYS: tuple[str, ...] = ("path", "file_path")


def extract_file_path(tool_call: dict[str, Any] | Any) -> str | None:
    """Pull the target file path from a tool call.

    Accepts either a dict-shaped tool call (``{"name": ..., "args":
    {...}}``) or any object with an ``args`` attribute. Returns the
    first non-empty string under any of the recognised path keys, or
    ``None`` when none match.

    The ``str | None`` shape mirrors what every caller used to
    open-code; centralising it here keeps the path semantics
    consistent so a future arg-name change touches one file.
    """
    args: Any
    if isinstance(tool_call, dict):
        args = tool_call.get("args")
    else:
        args = getattr(tool_call, "args", None)
    if not isinstance(args, dict):
        return None
    for key in _PATH_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


__all__ = [
    "EDIT_TOOLS",
    "FILE_MUTATING_TOOLS",
    "WRITE_TOOLS",
    "extract_file_path",
]
