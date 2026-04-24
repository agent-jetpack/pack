"""Tests for ScopeEnforcementMiddleware (Phase A.2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from langchain_core.messages import ToolMessage

from deepagents_cli.policy import TaskPolicy, policy_for
from deepagents_cli.scope_enforcement import (
    ScopeEnforcementMiddleware,
    _matches_any,
    _path_from_args,
)


def _tool_msg(content: str, status: str = "success") -> ToolMessage:
    msg = ToolMessage(content=content, name="write_file", tool_call_id="tc-1")
    msg.status = status
    return msg


def _write_request(path: str, tool_name: str = "write_file") -> Any:
    req = Mock()
    req.tool_call = {
        "name": tool_name,
        "args": {"path": path, "content": "x"},
        "id": "tc-1",
    }
    return req


def _other_request(tool_name: str = "read_file") -> Any:
    req = Mock()
    req.tool_call = {
        "name": tool_name,
        "args": {"path": "/app/file.py"},
        "id": "tc-1",
    }
    return req


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_path_from_args_reads_path_key() -> None:
    call = {"args": {"path": "/app/foo.py"}}
    assert _path_from_args(call) == "/app/foo.py"


def test_path_from_args_falls_back_to_file_path() -> None:
    call = {"args": {"file_path": "/app/foo.py"}}
    assert _path_from_args(call) == "/app/foo.py"


def test_path_from_args_returns_none_for_missing() -> None:
    assert _path_from_args({"args": {}}) is None
    assert _path_from_args({}) is None


def test_matches_any_basic_glob() -> None:
    assert _matches_any("docs/README.md", ("docs/**",)) is True
    assert _matches_any("src/foo.py", ("docs/**",)) is False


def test_matches_any_strips_leading_slash() -> None:
    # Absolute container paths should match repo-relative globs
    assert _matches_any("/app/docs/README.md", ("docs/**",)) is True


def test_matches_any_suffix_patterns() -> None:
    assert _matches_any("notes/foo.md", ("**/*.md",)) is True
    assert _matches_any("foo.md", ("**/*.md",)) is True


# ---------------------------------------------------------------------------
# Middleware: inactive when policy is None or disabled
# ---------------------------------------------------------------------------


def test_no_policy_means_noop() -> None:
    m = ScopeEnforcementMiddleware(policy=None)

    called = False

    def handler(_req: Any) -> ToolMessage:
        nonlocal called
        called = True
        return _tool_msg("ok")

    m.wrap_tool_call(_write_request("/arbitrary/path.py"), handler)
    assert called is True


def test_disabled_flag_skips_check() -> None:
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    m = ScopeEnforcementMiddleware(policy=p, disabled=True)

    handler_called = False

    def handler(_req: Any) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return _tool_msg("ok")

    # Would normally be rejected
    m.wrap_tool_call(_write_request("/src/naughty.py"), handler)
    assert handler_called is True


# ---------------------------------------------------------------------------
# Write rejection
# ---------------------------------------------------------------------------


def test_rejects_write_outside_allowed_paths() -> None:
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**", "**/*.md"))
    m = ScopeEnforcementMiddleware(policy=p)

    called = False

    def handler(_req: Any) -> ToolMessage:
        nonlocal called
        called = True
        return _tool_msg("ok")

    result = m.wrap_tool_call(_write_request("/src/foo.py"), handler)
    assert called is False  # handler blocked
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "Scope violation" in str(result.content)
    assert "docs" in str(result.content)


def test_allows_write_inside_allowed_paths() -> None:
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    m = ScopeEnforcementMiddleware(policy=p)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("file written")

    result = m.wrap_tool_call(_write_request("/app/docs/readme.md"), handler)
    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_edit_file_is_gated_same_as_write() -> None:
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    m = ScopeEnforcementMiddleware(policy=p)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    # edit outside docs → rejected
    result = m.wrap_tool_call(
        _write_request("/src/file.py", tool_name="edit_file"), handler
    )
    assert result.status == "error"


def test_read_file_is_not_gated() -> None:
    # Reads are always allowed — scope is about writes.
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    m = ScopeEnforcementMiddleware(policy=p)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("file content")

    result = m.wrap_tool_call(_other_request("read_file"), handler)
    assert result.status != "error"


def test_execute_is_not_gated() -> None:
    # Shell is gated by a different middleware; scope enforcement
    # stays in its lane.
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    m = ScopeEnforcementMiddleware(policy=p)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("executed")

    result = m.wrap_tool_call(_other_request("execute"), handler)
    assert result.status != "error"


# ---------------------------------------------------------------------------
# max_files_changed cap
# ---------------------------------------------------------------------------


def test_max_files_cap_blocks_new_files_beyond_limit() -> None:
    p = TaskPolicy(
        task_type="docs",
        allowed_paths=("**",),
        max_files_changed=2,
    )
    m = ScopeEnforcementMiddleware(policy=p)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    # First two distinct files: allowed
    r1 = m.wrap_tool_call(_write_request("/a.md"), handler)
    r2 = m.wrap_tool_call(_write_request("/b.md"), handler)
    assert r1.status != "error"
    assert r2.status != "error"

    # Third distinct file: blocked
    r3 = m.wrap_tool_call(_write_request("/c.md"), handler)
    assert r3.status == "error"
    assert "max_files_changed" in str(r3.content)


def test_max_files_cap_allows_revisits_to_same_file() -> None:
    p = TaskPolicy(
        task_type="docs",
        allowed_paths=("**",),
        max_files_changed=1,
    )
    m = ScopeEnforcementMiddleware(policy=p)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    # First touch of a.md — allowed
    assert m.wrap_tool_call(_write_request("/a.md"), handler).status != "error"
    # Second touch of same file — still allowed (not a new distinct file)
    assert m.wrap_tool_call(_write_request("/a.md"), handler).status != "error"
    # Touch of b.md — blocked (cap is 1)
    assert m.wrap_tool_call(_write_request("/b.md"), handler).status == "error"


def test_failed_writes_do_not_count_toward_cap() -> None:
    # The design says: only successful writes increment the count, so a
    # single typo doesn't burn the agent's file budget.
    p = TaskPolicy(
        task_type="docs",
        allowed_paths=("**",),
        max_files_changed=1,
    )
    m = ScopeEnforcementMiddleware(policy=p)

    def failing_handler(_req: Any) -> ToolMessage:
        return _tool_msg("write failed", status="error")

    def ok_handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    # First write fails (handler returns error status)
    m.wrap_tool_call(_write_request("/a.md"), failing_handler)
    # Cap should still allow another new file
    r2 = m.wrap_tool_call(_write_request("/b.md"), ok_handler)
    assert r2.status != "error"


# ---------------------------------------------------------------------------
# Recorder hook
# ---------------------------------------------------------------------------


def test_violation_recorder_called_on_rejection() -> None:
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    recorded: list[tuple[str, str, str]] = []

    def record(tool: str, path: str, reason: str) -> None:
        recorded.append((tool, path, reason))

    m = ScopeEnforcementMiddleware(policy=p, violation_recorder=record)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    m.wrap_tool_call(_write_request("/src/naughty.py"), handler)
    assert len(recorded) == 1
    assert recorded[0][0] == "write_file"
    assert recorded[0][1] == "/src/naughty.py"
    assert "out-of-scope" in recorded[0][2]


def test_violation_recorder_not_called_on_success() -> None:
    p = TaskPolicy(task_type="docs", allowed_paths=("**",))
    recorded: list = []

    def record(*args: Any) -> None:
        recorded.append(args)

    m = ScopeEnforcementMiddleware(policy=p, violation_recorder=record)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    m.wrap_tool_call(_write_request("/a.md"), handler)
    assert recorded == []


# ---------------------------------------------------------------------------
# Integration: classifier → policy → scope enforcement
# ---------------------------------------------------------------------------


def test_explicit_docs_task_type_policy_blocks_code_writes() -> None:
    # Route via explicit task_type hint (the harness sets this directly
    # when it's already decided the task is docs work). The classifier's
    # phase/domain path doesn't always infer docs from free-form prompts
    # like "Update the README" — that's a known gap, not a bug here.
    hints = {"task_type": "docs"}
    policy = policy_for(hints)
    assert policy.task_type == "docs"

    m = ScopeEnforcementMiddleware(policy=policy)

    def handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    # Under docs policy, writing to code is rejected
    result = m.wrap_tool_call(_write_request("/src/cli.py"), handler)
    assert result.status == "error"


async def test_async_wrap_enforces_scope() -> None:
    p = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    m = ScopeEnforcementMiddleware(policy=p)

    async def handler(_req: Any) -> ToolMessage:
        return _tool_msg("ok")

    result = await m.awrap_tool_call(_write_request("/src/bad.py"), handler)
    assert result.status == "error"
