"""Tests for specialized agent types."""

from __future__ import annotations

import pytest

from deepagents.agents.profiles import AgentProfile, AgentType, detect_agent_type, get_profile


class TestAgentProfile:
    def test_explore_is_read_only(self) -> None:
        profile = get_profile(AgentType.EXPLORE)
        assert profile.is_tool_allowed("read_file")
        assert profile.is_tool_allowed("glob")
        assert profile.is_tool_allowed("grep")
        assert not profile.is_tool_allowed("write_file")
        assert not profile.is_tool_allowed("edit_file")
        assert not profile.is_tool_allowed("execute")

    def test_explore_uses_cheap_model(self) -> None:
        profile = get_profile(AgentType.EXPLORE)
        assert profile.model_tier == "cheap"

    def test_plan_has_planning_tools(self) -> None:
        profile = get_profile(AgentType.PLAN)
        assert profile.is_tool_allowed("write_todos")
        assert profile.is_tool_allowed("ask_user")
        assert profile.is_tool_allowed("task")
        assert profile.is_tool_allowed("read_file")
        assert not profile.is_tool_allowed("write_file")
        assert not profile.is_tool_allowed("execute")

    def test_review_can_run_tests(self) -> None:
        profile = get_profile(AgentType.REVIEW)
        assert profile.is_tool_allowed("execute")
        assert profile.is_tool_allowed("read_file")
        assert not profile.is_tool_allowed("write_file")
        assert not profile.is_tool_allowed("edit_file")

    def test_general_has_all_tools(self) -> None:
        profile = get_profile(AgentType.GENERAL)
        assert profile.is_tool_allowed("read_file")
        assert profile.is_tool_allowed("write_file")
        assert profile.is_tool_allowed("execute")
        assert profile.is_tool_allowed("task")

    def test_each_type_has_system_prompt(self) -> None:
        for agent_type in AgentType:
            profile = get_profile(agent_type)
            assert len(profile.system_prompt) > 50

    def test_profiles_are_frozen(self) -> None:
        profile = get_profile(AgentType.EXPLORE)
        with pytest.raises(AttributeError):
            profile.name = "hacked"  # type: ignore[misc]


class TestGetProfile:
    def test_by_enum(self) -> None:
        profile = get_profile(AgentType.EXPLORE)
        assert profile.agent_type == AgentType.EXPLORE

    def test_by_string(self) -> None:
        profile = get_profile("explore")
        assert profile.agent_type == AgentType.EXPLORE

    def test_by_string_case_insensitive(self) -> None:
        profile = get_profile("REVIEW")
        assert profile.agent_type == AgentType.REVIEW

    def test_unknown_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown agent type"):
            get_profile("nonexistent")


class TestDetectAgentType:
    def test_explore_detection(self) -> None:
        assert detect_agent_type("find the auth module") == AgentType.EXPLORE
        assert detect_agent_type("where is the login function?") == AgentType.EXPLORE
        assert detect_agent_type("search for API endpoints") == AgentType.EXPLORE

    def test_review_detection(self) -> None:
        assert detect_agent_type("review this PR") == AgentType.REVIEW
        assert detect_agent_type("audit the auth code") == AgentType.REVIEW
        assert detect_agent_type("security review of the API") == AgentType.REVIEW

    def test_plan_detection(self) -> None:
        assert detect_agent_type("plan the implementation") == AgentType.PLAN
        assert detect_agent_type("design the architecture") == AgentType.PLAN
        assert detect_agent_type("break down this feature") == AgentType.PLAN

    def test_general_fallback(self) -> None:
        assert detect_agent_type("implement user registration") == AgentType.GENERAL
        assert detect_agent_type("fix the login bug") == AgentType.GENERAL
        assert detect_agent_type("add dark mode support") == AgentType.GENERAL

    def test_explore_with_action_words_goes_general(self) -> None:
        # "find" alone → explore, but "find and fix" → general
        assert detect_agent_type("find and fix the bug") == AgentType.GENERAL
