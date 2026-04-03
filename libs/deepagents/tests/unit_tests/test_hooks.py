"""Tests for the rich hook system.

Covers event dispatch, tool filtering, template substitution, blocking
hooks, timeout enforcement, output injection, and multi-hook ordering.
"""

from __future__ import annotations

import sys

from deepagents.hooks import HookDefinition, HookEngine, HookEvent
from deepagents.hooks.runners import substitute_template


async def test_hook_fires_on_correct_event() -> None:
    """A hook registered for PRE_TOOL_CALL fires only on that event."""
    engine = HookEngine([
        HookDefinition(event=HookEvent.PRE_TOOL_CALL, command="echo fired"),
    ])
    results = await engine.fire(HookEvent.PRE_TOOL_CALL)
    assert len(results) == 1
    assert results[0].return_code == 0
    assert "fired" in results[0].stdout

    # Different event produces no results.
    results = await engine.fire(HookEvent.SESSION_START)
    assert results == []


async def test_tool_filter_restricts_hooks() -> None:
    """A hook with tool_filter only fires when tool_name matches."""
    engine = HookEngine([
        HookDefinition(
            event=HookEvent.PRE_TOOL_CALL,
            command="echo matched",
            tool_filter="bash",
        ),
    ])

    # Matching tool name.
    results = await engine.fire(
        HookEvent.PRE_TOOL_CALL,
        {"tool_name": "bash"},
    )
    assert len(results) == 1
    assert results[0].return_code == 0

    # Non-matching tool name.
    results = await engine.fire(
        HookEvent.PRE_TOOL_CALL,
        {"tool_name": "grep"},
    )
    assert results == []


async def test_template_variable_substitution() -> None:
    """Template placeholders are replaced with sanitized context values."""
    result = substitute_template(
        "echo {tool_name} {file_path}",
        {"tool_name": "bash", "file_path": "/home/user/test.py"},
    )
    assert "bash" in result
    assert "/home/user/test.py" in result


async def test_template_injection_prevention() -> None:
    """Malicious template values are shell-quoted to prevent injection."""
    result = substitute_template(
        "echo {tool_name}",
        {"tool_name": "bash; rm -rf /"},
    )
    # The value should be quoted, not executed as multiple commands.
    assert ";" not in result or "'" in result


async def test_template_missing_variable() -> None:
    """Missing context variables substitute as empty quoted strings."""
    result = substitute_template("echo {tool_name}", {})
    assert "''" in result


async def test_blocking_hook_prevents_execution() -> None:
    """A blocking hook with non-zero exit stops further hook execution."""
    engine = HookEngine([
        HookDefinition(
            event=HookEvent.PRE_TOOL_CALL,
            command="exit 1",
            blocking=True,
        ),
        HookDefinition(
            event=HookEvent.PRE_TOOL_CALL,
            command="echo should-not-run",
        ),
    ])
    results = await engine.fire(HookEvent.PRE_TOOL_CALL)
    # Only the blocking hook should have run.
    assert len(results) == 1
    assert results[0].return_code == 1


async def test_blocking_hook_success_continues() -> None:
    """A blocking hook that succeeds allows subsequent hooks to run."""
    engine = HookEngine([
        HookDefinition(
            event=HookEvent.PRE_TOOL_CALL,
            command="exit 0",
            blocking=True,
        ),
        HookDefinition(
            event=HookEvent.PRE_TOOL_CALL,
            command="echo second",
        ),
    ])
    results = await engine.fire(HookEvent.PRE_TOOL_CALL)
    assert len(results) == 2
    assert results[1].return_code == 0


async def test_timeout_kills_long_running_hooks() -> None:
    """A hook exceeding its timeout is killed and returns -1."""
    engine = HookEngine([
        HookDefinition(
            event=HookEvent.PRE_TOOL_CALL,
            command=f'{sys.executable} -c "import time; time.sleep(30)"',
            timeout=1,
        ),
    ])
    results = await engine.fire(HookEvent.PRE_TOOL_CALL)
    assert len(results) == 1
    assert results[0].return_code == -1
    assert "timed out" in results[0].stderr.lower()


async def test_inject_output_flag() -> None:
    """The inject flag on HookResult matches the hook's inject_output setting."""
    engine = HookEngine([
        HookDefinition(
            event=HookEvent.POST_TOOL_CALL,
            command="echo injected-output",
            inject_output=True,
        ),
        HookDefinition(
            event=HookEvent.POST_TOOL_CALL,
            command="echo silent-output",
            inject_output=False,
        ),
    ])
    results = await engine.fire(HookEvent.POST_TOOL_CALL)
    assert len(results) == 2
    assert results[0].inject is True
    assert "injected-output" in results[0].stdout
    assert results[1].inject is False


async def test_multiple_hooks_execute_in_order() -> None:
    """Multiple hooks for the same event execute in registration order."""
    engine = HookEngine([
        HookDefinition(
            event=HookEvent.SESSION_START,
            command="echo first",
        ),
        HookDefinition(
            event=HookEvent.SESSION_START,
            command="echo second",
        ),
        HookDefinition(
            event=HookEvent.SESSION_START,
            command="echo third",
        ),
    ])
    results = await engine.fire(HookEvent.SESSION_START)
    assert len(results) == 3
    assert "first" in results[0].stdout
    assert "second" in results[1].stdout
    assert "third" in results[2].stdout


async def test_hook_result_captures_stderr() -> None:
    """Standard error from hook commands is captured in the result."""
    engine = HookEngine([
        HookDefinition(
            event=HookEvent.ERROR_OCCURRED,
            command="echo error-msg >&2",
        ),
    ])
    results = await engine.fire(HookEvent.ERROR_OCCURRED)
    assert len(results) == 1
    assert "error-msg" in results[0].stderr


async def test_all_hook_events_exist() -> None:
    """All specified hook events are defined in the enum."""
    expected = {
        "PRE_TOOL_CALL",
        "POST_TOOL_CALL",
        "PRE_MODEL_CALL",
        "POST_MODEL_CALL",
        "SESSION_START",
        "SESSION_END",
        "FILE_MODIFIED",
        "COMPACTION_TRIGGERED",
        "PERMISSION_DECIDED",
        "ERROR_OCCURRED",
    }
    actual = {e.name for e in HookEvent}
    assert expected == actual


async def test_empty_engine_returns_no_results() -> None:
    """An engine with no hooks returns empty results for any event."""
    engine = HookEngine()
    results = await engine.fire(HookEvent.SESSION_START)
    assert results == []
