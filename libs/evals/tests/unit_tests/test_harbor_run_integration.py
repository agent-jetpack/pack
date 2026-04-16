"""Integration tests for DeepAgentsWrapper.run().

Covers the try/finally orchestration between _invoke_with_retry, failure
capture, _save_trajectory, and LangSmith trace annotation — behavior that
component-level tests in test_harbor_retry.py and
test_harbor_partial_trajectory.py do not exercise.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeTrial:
    def __init__(self, tmp_path: Path) -> None:
        self.config_path = tmp_path / "config.json"
        self.config_path.write_text('{"model": "test"}')
        self.agent_dir = tmp_path


class _FakeEnvironment:
    def __init__(self, tmp_path: Path) -> None:
        self.session_id = "integration-session"
        self.trial_paths = _FakeTrial(tmp_path)

    async def run_command(self, _cmd: str, timeout: int = 30) -> Any:  # noqa: ARG002
        return SimpleNamespace(stdout="", stderr="", return_code=0)


class _FakeContext:
    pass


class _FakeRunTree:
    """Minimal LangSmith RunTree stand-in for trace context."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.ended_with: dict[str, Any] = {}

    def end(self, **kwargs: Any) -> None:
        self.ended_with.update(kwargs)


class _FakeTraceCM:
    """Context manager that snapshots metadata at entry (worst case)."""

    def __init__(self, *, metadata: dict[str, Any], **_kw: Any) -> None:
        # Intentionally deep-copy: simulates a LangSmith version that
        # snapshots metadata at trace creation. The mirror helper must
        # still push retry annotations through to run_tree.metadata.
        self._snapshot = dict(metadata)
        self.run_tree = _FakeRunTree()
        self.run_tree.metadata = self._snapshot

    def __enter__(self) -> _FakeRunTree:
        return self.run_tree

    def __exit__(self, *_args: Any) -> None:
        return None


@pytest.fixture
def wrapper(tmp_path: Path):
    """DeepAgentsWrapper bypassing __init__ so no real model is loaded."""
    from deepagents_harbor.deepagents_wrapper import DeepAgentsWrapper

    instance = DeepAgentsWrapper.__new__(DeepAgentsWrapper)
    instance._model_name = "test-model"
    instance._model = MagicMock()
    instance._use_cli_agent = False  # SDK path is simpler to fake
    instance._instruction_to_example_id = {}
    instance.logs_dir = tmp_path
    return instance


@pytest.fixture
def fake_env(tmp_path: Path):
    return _FakeEnvironment(tmp_path)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Remove real sleeps in any retry-triggered path."""
    import asyncio
    import random

    async def _zero_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _zero_sleep)
    monkeypatch.setattr(random, "uniform", lambda _a, _b: 0.0)


@pytest.fixture
def stub_collect_sandbox_metadata(monkeypatch):
    """Skip real sandbox metadata collection; orthogonal to retry logic."""
    from deepagents_harbor import deepagents_wrapper as mod

    async def _stub(_backend: Any) -> None:
        return None

    monkeypatch.setattr(mod, "collect_sandbox_metadata", _stub)


@pytest.fixture
def stub_backend(monkeypatch):
    """Stub HarborSandbox construction (avoids real environment wiring)."""
    from deepagents_harbor import deepagents_wrapper as mod

    monkeypatch.setattr(mod, "HarborSandbox", lambda env: env)


@pytest.fixture
def stub_system_prompt(monkeypatch, wrapper):
    """Bypass directory-context system prompt construction."""

    async def _stub(_self, _backend):
        return "system prompt"

    monkeypatch.setattr(
        wrapper.__class__, "_get_formatted_system_prompt", _stub
    )


@pytest.fixture
def stub_create_deep_agent(monkeypatch):
    """Replace create_deep_agent with a factory returning a scripted agent."""
    from deepagents_harbor import deepagents_wrapper as mod

    created: list[Any] = []

    def _factory(agent: Any):
        def _create_deep_agent(**_kwargs: Any) -> Any:
            created.append(agent)
            return agent

        monkeypatch.setattr(mod, "create_deep_agent", _create_deep_agent)
        return created

    return _factory


@pytest.mark.asyncio
async def test_run_success_saves_trajectory_without_failure(
    wrapper,
    fake_env,
    stub_collect_sandbox_metadata,
    stub_backend,
    stub_system_prompt,
    stub_create_deep_agent,
):
    """Happy path: trajectory.json saved, no failure block."""
    from langchain_core.messages import AIMessage

    agent = MagicMock()
    agent.ainvoke = AsyncMock(
        return_value={"messages": [AIMessage(content="done")]}
    )
    stub_create_deep_agent(agent)

    await wrapper.run("solve it", fake_env, _FakeContext())

    trajectory = json.loads((wrapper.logs_dir / "trajectory.json").read_text())
    assert trajectory.get("extra") is None or "failure" not in trajectory.get(
        "extra", {}
    )


@pytest.mark.asyncio
async def test_run_empty_messages_does_not_crash(
    wrapper,
    fake_env,
    stub_collect_sandbox_metadata,
    stub_backend,
    stub_system_prompt,
    stub_create_deep_agent,
):
    """Agent returns {'messages': []} — must not raise IndexError.

    Guards the P1 fix. Previously the LangSmith branch (and the structure
    after it) indexed ``result['messages'][-1]`` unconditionally.
    """
    agent = MagicMock()
    agent.ainvoke = AsyncMock(return_value={"messages": []})
    stub_create_deep_agent(agent)

    # Should return cleanly; no IndexError propagated.
    await wrapper.run("task", fake_env, _FakeContext())

    assert (wrapper.logs_dir / "trajectory.json").exists()


@pytest.mark.asyncio
async def test_run_terminal_failure_saves_partial_trajectory(
    wrapper,
    fake_env,
    stub_collect_sandbox_metadata,
    stub_backend,
    stub_system_prompt,
    stub_create_deep_agent,
):
    """All retries exhausted -> trajectory with status=failed, exception re-raised."""
    agent = MagicMock()
    agent.ainvoke = AsyncMock(side_effect=ConnectionError("disconnected"))
    stub_create_deep_agent(agent)

    with pytest.raises(ConnectionError):
        await wrapper.run("task", fake_env, _FakeContext())

    trajectory = json.loads((wrapper.logs_dir / "trajectory.json").read_text())
    assert trajectory["extra"]["status"] == "failed"
    assert trajectory["extra"]["failure"]["reason"] == "retry_exhausted"
    assert trajectory["extra"]["failure"]["exception_type"] == "ConnectionError"


@pytest.mark.asyncio
async def test_run_non_retryable_error_saves_partial_trajectory(
    wrapper,
    fake_env,
    stub_collect_sandbox_metadata,
    stub_backend,
    stub_system_prompt,
    stub_create_deep_agent,
):
    """Non-retryable -> trajectory.failure.reason=non_retryable, attempts=1."""
    agent = MagicMock()
    agent.ainvoke = AsyncMock(side_effect=ValueError("bad input"))
    stub_create_deep_agent(agent)

    with pytest.raises(ValueError):
        await wrapper.run("task", fake_env, _FakeContext())

    trajectory = json.loads((wrapper.logs_dir / "trajectory.json").read_text())
    assert trajectory["extra"]["failure"]["reason"] == "non_retryable"
    assert trajectory["extra"]["failure"]["attempts"] == 1


@pytest.mark.asyncio
async def test_run_save_trajectory_failure_does_not_mask_original_exception(
    wrapper,
    fake_env,
    stub_collect_sandbox_metadata,
    stub_backend,
    stub_system_prompt,
    stub_create_deep_agent,
    monkeypatch,
):
    """Secondary persistence failure is swallowed; original exc propagates."""
    agent = MagicMock()
    agent.ainvoke = AsyncMock(side_effect=ConnectionError("original"))
    stub_create_deep_agent(agent)

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(wrapper.__class__, "_save_trajectory", _boom)

    with pytest.raises(ConnectionError, match="original"):
        await wrapper.run("task", fake_env, _FakeContext())


@pytest.mark.asyncio
async def test_run_mirrors_retry_metadata_to_langsmith_trace(
    wrapper,
    fake_env,
    stub_collect_sandbox_metadata,
    stub_backend,
    stub_system_prompt,
    stub_create_deep_agent,
    monkeypatch,
):
    """LangSmith branch: retry annotations reach run_tree.metadata even when
    the trace context manager snapshots the dict at entry."""
    agent = MagicMock()
    agent.ainvoke = AsyncMock(
        side_effect=[ConnectionError("once"), {"messages": []}]
    )
    stub_create_deep_agent(agent)

    # Activate the LangSmith branch
    monkeypatch.setenv("LANGSMITH_EXPERIMENT", "test-experiment")

    captured: list[_FakeTraceCM] = []

    def _fake_trace(**kwargs: Any) -> _FakeTraceCM:
        cm = _FakeTraceCM(**kwargs)
        captured.append(cm)
        return cm

    from deepagents_harbor import deepagents_wrapper as mod

    monkeypatch.setattr(mod, "trace", _fake_trace)

    await wrapper.run("task", fake_env, _FakeContext())

    assert len(captured) == 1
    run_tree = captured[0].run_tree
    # Retry annotations must be present in the trace's metadata dict after
    # invocation, even though our fake trace snapshotted at entry.
    assert run_tree.metadata["retry_attempts"] == 2
    assert run_tree.metadata["retry_terminated"] is False
