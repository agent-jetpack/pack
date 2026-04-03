"""Tests for agent dispatch utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepagents.agents.profiles import AgentType, get_profile
from deepagents.coordination.teammate import ExecutionModel
from deepagents.middleware.pack.agent_dispatch import (
    _slugify,
    build_subagent_spec,
    create_teammate_config,
    resolve_agent_profile,
)


class TestResolveAgentProfile:
    """Tests for resolve_agent_profile."""

    def test_explicit_explore(self) -> None:
        profile = resolve_agent_profile("do something", agent_type="explore")
        assert profile.agent_type == AgentType.EXPLORE

    def test_explicit_plan(self) -> None:
        profile = resolve_agent_profile("do something", agent_type="plan")
        assert profile.agent_type == AgentType.PLAN

    def test_explicit_review(self) -> None:
        profile = resolve_agent_profile("do something", agent_type="review")
        assert profile.agent_type == AgentType.REVIEW

    def test_explicit_general(self) -> None:
        profile = resolve_agent_profile("do something", agent_type="general")
        assert profile.agent_type == AgentType.GENERAL

    def test_explicit_enum(self) -> None:
        profile = resolve_agent_profile("irrelevant", agent_type=AgentType.REVIEW)
        assert profile.agent_type == AgentType.REVIEW

    def test_explicit_overrides_detection(self) -> None:
        """Explicit type wins even if task description suggests otherwise."""
        profile = resolve_agent_profile(
            "find all files matching pattern",
            agent_type="review",
        )
        assert profile.agent_type == AgentType.REVIEW

    def test_auto_detect_explore(self) -> None:
        profile = resolve_agent_profile("find where the config file is located")
        assert profile.agent_type == AgentType.EXPLORE

    def test_auto_detect_plan(self) -> None:
        profile = resolve_agent_profile("plan the architecture for the new module")
        assert profile.agent_type == AgentType.PLAN

    def test_auto_detect_review(self) -> None:
        profile = resolve_agent_profile("review the code for security issues")
        assert profile.agent_type == AgentType.REVIEW

    def test_auto_detect_general(self) -> None:
        profile = resolve_agent_profile("implement the new authentication feature")
        assert profile.agent_type == AgentType.GENERAL

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown agent type"):
            resolve_agent_profile("do something", agent_type="nonexistent")

    def test_returns_matching_profile(self) -> None:
        """Resolved profile matches the canonical get_profile output."""
        profile = resolve_agent_profile("explore the codebase")
        expected = get_profile(AgentType.EXPLORE)
        assert profile == expected


class TestBuildSubagentSpec:
    """Tests for build_subagent_spec."""

    def test_spec_has_required_fields(self) -> None:
        spec = build_subagent_spec("find the bug")
        assert "name" in spec
        assert "description" in spec
        assert "system_prompt" in spec

    def test_spec_name_matches_type(self) -> None:
        spec = build_subagent_spec("anything", agent_type="review")
        assert spec["name"] == "review"

    def test_auto_detected_spec(self) -> None:
        spec = build_subagent_spec("plan the migration strategy")
        assert spec["name"] == "plan"
        profile = get_profile(AgentType.PLAN)
        assert spec["system_prompt"] == profile.system_prompt

    def test_spec_description_from_profile(self) -> None:
        spec = build_subagent_spec("x", agent_type="explore")
        profile = get_profile(AgentType.EXPLORE)
        assert spec["description"] == profile.description


class TestSlugify:
    """Tests for _slugify helper."""

    def test_basic(self) -> None:
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self) -> None:
        assert _slugify("fix: the bug!") == "fix-the-bug"

    def test_max_length(self) -> None:
        result = _slugify("a very long description " * 5, max_length=20)
        assert len(result) <= 20

    def test_no_trailing_hyphen(self) -> None:
        result = _slugify("hello world!", max_length=6)
        assert not result.endswith("-")

    def test_collapses_hyphens(self) -> None:
        assert _slugify("a - - b") == "a-b"


class TestCreateTeammateConfig:
    """Tests for create_teammate_config."""

    def test_fork_mode_defaults(self) -> None:
        config = create_teammate_config("worker-1", "implement the feature")
        assert config.execution_model == ExecutionModel.FORK
        assert config.agent_id == "worker-1"
        assert config.worktree_branch is None

    def test_worktree_generates_branch(self) -> None:
        config = create_teammate_config(
            "worker-1",
            "fix the login bug",
            execution_model="worktree",
        )
        assert config.execution_model == ExecutionModel.WORKTREE
        assert config.worktree_branch is not None
        assert config.worktree_branch.startswith("agent/worker-1/")
        assert "fix" in config.worktree_branch
        assert "login" in config.worktree_branch

    def test_worktree_branch_is_slug(self) -> None:
        config = create_teammate_config(
            "w1",
            "Fix: The UGLY Bug!!",
            execution_model=ExecutionModel.WORKTREE,
        )
        branch = config.worktree_branch
        assert branch is not None
        # Should be lowercase, no special chars
        assert branch == branch.lower()
        assert "!" not in branch
        assert ":" not in branch

    def test_teammate_mode_requires_mailbox_dir(self) -> None:
        with pytest.raises(ValueError, match="mailbox_directory is required"):
            create_teammate_config(
                "worker-1",
                "do something",
                execution_model="teammate",
            )

    def test_teammate_mode_with_mailbox(self, tmp_path: Path) -> None:
        config = create_teammate_config(
            "worker-1",
            "implement the auth module",
            execution_model="teammate",
            mailbox_directory=tmp_path,
        )
        assert config.execution_model == ExecutionModel.TEAMMATE

    def test_agent_type_auto_detected(self) -> None:
        config = create_teammate_config("scout", "explore the codebase structure")
        assert config.agent_type == "explore"

    def test_system_prompt_from_profile(self) -> None:
        config = create_teammate_config("reviewer", "review the code for quality")
        profile = get_profile(AgentType.REVIEW)
        assert config.system_prompt == profile.system_prompt

    def test_max_turns_from_profile(self) -> None:
        config = create_teammate_config("scout", "find the config files")
        profile = get_profile(AgentType.EXPLORE)
        assert config.max_turns == profile.max_turns

    def test_string_execution_model(self) -> None:
        config = create_teammate_config("w", "do stuff", execution_model="fork")
        assert config.execution_model == ExecutionModel.FORK
