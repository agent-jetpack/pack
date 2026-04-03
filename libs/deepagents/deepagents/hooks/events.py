"""Hook event types for the rich hook system.

Each event represents a lifecycle point where external commands can be
triggered. Events are intentionally coarse-grained so that hook authors
can filter by tool name when finer control is needed.
"""

from __future__ import annotations

from enum import Enum


class HookEvent(Enum):
    """Lifecycle events that can trigger hook execution.

    Events cover model calls, tool calls, session boundaries, file
    modifications, compaction, permission decisions, and errors.
    """

    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    PRE_MODEL_CALL = "pre_model_call"
    POST_MODEL_CALL = "post_model_call"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    FILE_MODIFIED = "file_modified"
    COMPACTION_TRIGGERED = "compaction_triggered"
    PERMISSION_DECIDED = "permission_decided"
    ERROR_OCCURRED = "error_occurred"
