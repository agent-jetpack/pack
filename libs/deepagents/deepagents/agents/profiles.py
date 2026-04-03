"""Agent profiles defining scoped tools, prompts, and model routing.

Each profile constrains an agent to a specific set of tools and
provides a tailored system prompt for its role. Tool scoping is
enforced at dispatch time — the agent literally cannot call tools
outside its profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AgentType(str, Enum):
    """Available agent specializations."""

    EXPLORE = "explore"
    PLAN = "plan"
    REVIEW = "review"
    GENERAL = "general"


@dataclass(frozen=True)
class AgentProfile:
    """Configuration for a specialized agent.

    Args:
        agent_type: The specialization type.
        name: Human-readable name.
        description: What this agent does (shown in task delegation).
        system_prompt: Tailored instructions for this role.
        allowed_tools: Whitelist of tool names this agent can use.
        model_tier: Model routing hint — "cheap" for fast/cheap model,
            "main" for the user's primary model.
        max_turns: Maximum turns before the agent must return.
    """

    agent_type: AgentType
    name: str
    description: str
    system_prompt: str
    allowed_tools: frozenset[str]
    model_tier: str = "main"
    max_turns: int = 50

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is within this profile's scope.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            True if the tool is allowed for this agent type.
        """
        return tool_name in self.allowed_tools


# Read-only tools available to all agents
_READ_TOOLS: frozenset[str] = frozenset({
    "read_file", "ls", "glob", "grep", "web_search", "fetch_url",
})

# Planning tools
_PLAN_TOOLS: frozenset[str] = _READ_TOOLS | frozenset({
    "write_todos", "ask_user", "task",
})

# Review tools — can read and run tests
_REVIEW_TOOLS: frozenset[str] = _READ_TOOLS | frozenset({
    "execute", "write_todos",
})

# All tools — no restrictions
_ALL_TOOLS: frozenset[str] = frozenset({
    "read_file", "ls", "glob", "grep", "web_search", "fetch_url",
    "write_file", "edit_file", "execute", "task",
    "write_todos", "ask_user", "compact_conversation",
    "launch_async_subagent", "update_async_subagent", "cancel_async_subagent",
    "git_worktree_create", "git_worktree_list", "git_worktree_remove",
    "read_pdf", "read_image",
})


_EXPLORE_PROMPT = """You are an Explore agent — a fast, focused codebase investigator.

Your job is to quickly find and understand code. You CANNOT modify files.

## Approach
- Search broadly first (glob, grep), then read specific files
- Look for patterns, conventions, and architecture
- Report findings concisely — file paths, key functions, patterns observed
- If you find what was asked for, stop. Don't explore further than needed.

## Constraints
- You have READ-ONLY access. Do not attempt to write or execute anything.
- Be fast. Use glob and grep before reading entire files.
- Report findings, don't implement solutions."""

_PLAN_PROMPT = """You are a Plan agent — a technical architect and task planner.

Your job is to break down complex tasks into actionable steps.

## Approach
- Read relevant code to understand the current architecture
- Identify dependencies, risks, and decision points
- Create a structured plan using write_todos
- Ask the user for clarification on ambiguous requirements

## Output
- Use write_todos to create a clear, dependency-ordered task list
- Each task should be specific enough for an implementer to start without guessing
- Note files to create/modify, patterns to follow, and test scenarios

## Constraints
- Do NOT implement code. Plan only.
- You can read files and search, but cannot write or execute."""

_REVIEW_PROMPT = """You are a Review agent — a code reviewer focused on quality.

Your job is to review code changes for bugs, security issues, and quality.

## Focus Areas
1. **Correctness**: Logic errors, edge cases, off-by-one errors
2. **Security**: Injection vulnerabilities, secret exposure, unsafe operations
3. **Quality**: Code style, naming, duplication, complexity
4. **Testing**: Test coverage, missing edge case tests, flaky test patterns
5. **Architecture**: Does the change fit existing patterns?

## Approach
- Read the changed files and understand the context
- Run tests to verify they pass: use `execute` for test commands only
- Report findings with severity (critical/warning/suggestion)
- Be specific — cite file paths and line numbers

## Constraints
- You can read files and run tests, but cannot modify code.
- Only use `execute` for test commands (pytest, npm test, etc.)."""

_GENERAL_PROMPT = """You are a general-purpose coding agent with full tool access.

Work through tasks systematically: understand first, implement, then verify.
Follow existing code patterns and conventions."""


# Pre-built profiles
_PROFILES: dict[AgentType, AgentProfile] = {
    AgentType.EXPLORE: AgentProfile(
        agent_type=AgentType.EXPLORE,
        name="Explore",
        description="Fast codebase exploration with read-only tools. Uses a cheap model for speed.",
        system_prompt=_EXPLORE_PROMPT,
        allowed_tools=_READ_TOOLS,
        model_tier="cheap",
        max_turns=30,
    ),
    AgentType.PLAN: AgentProfile(
        agent_type=AgentType.PLAN,
        name="Plan",
        description="Technical planning and task decomposition. Reads code and creates structured plans.",
        system_prompt=_PLAN_PROMPT,
        allowed_tools=_PLAN_TOOLS,
        model_tier="main",
        max_turns=40,
    ),
    AgentType.REVIEW: AgentProfile(
        agent_type=AgentType.REVIEW,
        name="Review",
        description="Code review with read access and test execution. Finds bugs, security issues, and quality problems.",
        system_prompt=_REVIEW_PROMPT,
        allowed_tools=_REVIEW_TOOLS,
        model_tier="main",
        max_turns=40,
    ),
    AgentType.GENERAL: AgentProfile(
        agent_type=AgentType.GENERAL,
        name="General",
        description="General-purpose agent with full tool access for any coding task.",
        system_prompt=_GENERAL_PROMPT,
        allowed_tools=_ALL_TOOLS,
        model_tier="main",
        max_turns=50,
    ),
}


def get_profile(agent_type: AgentType | str) -> AgentProfile:
    """Get the profile for an agent type.

    Args:
        agent_type: Agent type enum or string name.

    Returns:
        The corresponding agent profile.

    Raises:
        ValueError: If the agent type is not recognized.
    """
    if isinstance(agent_type, str):
        try:
            agent_type = AgentType(agent_type.lower())
        except ValueError:
            msg = f"Unknown agent type: {agent_type!r}. Valid types: {[t.value for t in AgentType]}"
            raise ValueError(msg) from None

    profile = _PROFILES.get(agent_type)
    if profile is None:
        msg = f"No profile defined for agent type: {agent_type}"
        raise ValueError(msg)
    return profile


def detect_agent_type(task_description: str) -> AgentType:
    """Auto-detect the best agent type from a task description.

    Uses keyword matching to route tasks to specialized agents.

    Args:
        task_description: Natural language description of the task.

    Returns:
        The detected agent type.
    """
    lower = task_description.lower()

    # Explore signals
    explore_keywords = {"find", "search", "where", "look for", "locate", "explore", "what is", "show me", "list"}
    if any(kw in lower for kw in explore_keywords) and not any(kw in lower for kw in {"fix", "implement", "create", "add", "write", "build"}):
        return AgentType.EXPLORE

    # Review signals — must be about reviewing, not fixing
    review_keywords = {"review", "audit", "security review", "vulnerability scan", "code quality", "lint check"}
    action_keywords = {"fix", "implement", "create", "add", "write", "build", "update", "change", "modify"}
    has_action = any(kw in lower for kw in action_keywords)
    if any(kw in lower for kw in review_keywords) and not has_action:
        return AgentType.REVIEW

    # Plan signals
    plan_keywords = {"plan", "design", "architect", "break down", "decompose", "strategy", "approach"}
    if any(kw in lower for kw in plan_keywords):
        return AgentType.PLAN

    # Default to general
    return AgentType.GENERAL
