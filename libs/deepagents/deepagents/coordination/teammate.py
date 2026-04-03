"""Teammate configuration and execution model definitions.

Defines the three execution models for subagent delegation and
provides configuration for teammate-mode agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionModel(str, Enum):
    """How a subagent is executed relative to the parent."""

    FORK = "fork"            # Separate context, shared filesystem, runs to completion
    TEAMMATE = "teammate"    # Independent with async mailbox communication
    WORKTREE = "worktree"    # Full git worktree isolation


@dataclass
class TeammateConfig:
    """Configuration for a teammate-mode subagent.

    Args:
        agent_id: Unique identifier for this teammate.
        agent_type: Optional specialized agent type (explore, plan, review).
        execution_model: How the agent should be executed.
        worktree_branch: Git branch name for worktree mode.
        model_override: Optional model to use instead of parent's model.
        system_prompt: Optional custom system prompt.
        max_turns: Maximum turns before the agent must return.
    """

    agent_id: str
    agent_type: str | None = None
    execution_model: ExecutionModel = ExecutionModel.FORK
    worktree_branch: str | None = None
    model_override: str | None = None
    system_prompt: str | None = None
    max_turns: int = 50

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage or transmission."""
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "execution_model": self.execution_model.value,
            "worktree_branch": self.worktree_branch,
            "model_override": self.model_override,
            "max_turns": self.max_turns,
        }
