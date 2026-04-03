"""Agent dispatch utilities for wiring typed agents into the subagent system.

Provides helpers to resolve agent profiles from task descriptions and
create teammate configurations. These are utility functions used by the
CLI when constructing custom subagents — they do not modify the upstream
SubAgentMiddleware directly.
"""

from __future__ import annotations

import re
from pathlib import Path

from deepagents.agents.profiles import AgentProfile, AgentType, detect_agent_type, get_profile
from deepagents.coordination.mailbox import Mailbox
from deepagents.coordination.teammate import ExecutionModel, TeammateConfig
from deepagents.middleware.subagents import SubAgent


def resolve_agent_profile(
    task_description: str,
    *,
    agent_type: AgentType | str | None = None,
) -> AgentProfile:
    """Resolve an agent profile for a task.

    If `agent_type` is explicitly provided, returns that profile directly.
    Otherwise, auto-detects the best agent type from the task description
    using keyword matching.

    Args:
        task_description: Natural language description of the task.
        agent_type: Explicit agent type to use, bypassing auto-detection.

    Returns:
        The resolved agent profile with system_prompt and allowed_tools.
    """
    if agent_type is not None:
        return get_profile(agent_type)
    detected = detect_agent_type(task_description)
    return get_profile(detected)


def build_subagent_spec(
    task_description: str,
    *,
    agent_type: AgentType | str | None = None,
) -> SubAgent:
    """Build a SubAgent spec from a task description and optional agent type.

    Resolves the agent profile and constructs a SubAgent TypedDict with
    the profile's system_prompt and description. The caller is responsible
    for adding `model` and `tools` before passing to SubAgentMiddleware.

    Args:
        task_description: Natural language description of the task.
        agent_type: Explicit agent type, or None for auto-detection.

    Returns:
        A SubAgent spec with name, description, and system_prompt populated
        from the resolved profile.
    """
    profile = resolve_agent_profile(task_description, agent_type=agent_type)
    return SubAgent(
        name=profile.agent_type.value,
        description=profile.description,
        system_prompt=profile.system_prompt,
    )


def _slugify(text: str, *, max_length: int = 40) -> str:
    """Convert text to a git-branch-safe slug.

    Args:
        text: Input text to slugify.
        max_length: Maximum length of the slug.

    Returns:
        A lowercase, hyphen-separated slug safe for git branch names.
    """
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:max_length].rstrip("-")


def create_teammate_config(
    agent_id: str,
    task_description: str,
    *,
    execution_model: ExecutionModel | str = ExecutionModel.FORK,
    mailbox_directory: Path | str | None = None,
) -> TeammateConfig:
    """Create a TeammateConfig for a subagent with appropriate settings.

    For "worktree" mode, generates a branch name from the task description.
    For "teammate" mode, creates a Mailbox for the agent if a mailbox
    directory is provided.

    Args:
        agent_id: Unique identifier for this teammate.
        task_description: Description used to generate branch names and
            detect agent type.
        execution_model: How the agent should be executed.
        mailbox_directory: Base directory for mailboxes, required for
            teammate mode.

    Returns:
        A configured TeammateConfig.

    Raises:
        ValueError: If teammate mode is requested without a mailbox directory.
    """
    if isinstance(execution_model, str):
        execution_model = ExecutionModel(execution_model)

    profile = resolve_agent_profile(task_description)

    branch: str | None = None
    if execution_model == ExecutionModel.WORKTREE:
        branch = f"agent/{agent_id}/{_slugify(task_description)}"

    if execution_model == ExecutionModel.TEAMMATE:
        if mailbox_directory is None:
            msg = "mailbox_directory is required for teammate execution model"
            raise ValueError(msg)
        # Ensure the mailbox infrastructure exists for this agent
        Mailbox(mailbox_directory, agent_id)

    return TeammateConfig(
        agent_id=agent_id,
        agent_type=profile.agent_type.value,
        execution_model=execution_model,
        worktree_branch=branch,
        system_prompt=profile.system_prompt,
        max_turns=profile.max_turns,
    )
