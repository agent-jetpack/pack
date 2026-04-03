"""Permission classifier using deterministic rules with optional LLM fallback.

Stage 1 (deterministic): regex patterns matching known dangerous commands.
Stage 2 (optional LLM): chain-of-thought re-evaluation for ambiguous cases.

Follows the principle: deterministic before LLM. 90% of decisions are made
by regex patterns alone.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ClassifierDecision(str, Enum):
    """Result of the permission classifier."""

    ALLOW = "allow"
    SOFT_DENY = "soft_deny"  # Show user with reasoning, let them override
    HARD_DENY = "hard_deny"  # Block with explanation


@dataclass
class ClassifierResult:
    """Result from the permission classifier.

    Args:
        decision: The classification decision.
        reason: Human-readable explanation of why.
        stage: Which stage made the decision (1=deterministic, 2=llm).
    """

    decision: ClassifierDecision
    reason: str
    stage: int


# Patterns that indicate dangerous operations — always soft/hard deny
_DANGEROUS_PATTERNS: list[tuple[str, str, ClassifierDecision]] = [
    # Destructive file operations
    (r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|.*-[a-zA-Z]*f[a-zA-Z]*)", "Recursive/forced file deletion", ClassifierDecision.HARD_DENY),
    (r"rm\s+-rf\s+[/~]", "Recursive force delete from root or home", ClassifierDecision.HARD_DENY),
    # Git destructive operations
    (r"git\s+push\s+.*--force", "Force push can overwrite remote history", ClassifierDecision.SOFT_DENY),
    (r"git\s+reset\s+--hard", "Hard reset discards uncommitted changes", ClassifierDecision.SOFT_DENY),
    (r"git\s+clean\s+-[a-zA-Z]*f", "Git clean removes untracked files", ClassifierDecision.SOFT_DENY),
    (r"git\s+checkout\s+--\s+\.", "Checkout discards all changes", ClassifierDecision.SOFT_DENY),
    # Database destructive operations
    (r"DROP\s+(TABLE|DATABASE|SCHEMA)", "Database destructive operation", ClassifierDecision.HARD_DENY),
    (r"TRUNCATE\s+TABLE", "Table truncation", ClassifierDecision.HARD_DENY),
    (r"DELETE\s+FROM\s+\w+\s*;?\s*$", "Unbounded DELETE without WHERE", ClassifierDecision.SOFT_DENY),
    # System-level danger
    (r"chmod\s+777", "Setting world-writable permissions", ClassifierDecision.SOFT_DENY),
    (r"curl\s+.*\|\s*(bash|sh|zsh)", "Piping remote content to shell", ClassifierDecision.HARD_DENY),
    (r"eval\s*\(", "Dynamic code evaluation", ClassifierDecision.SOFT_DENY),
    # Secret exposure
    (r"(password|secret|token|api.key)\s*=\s*['\"]", "Potential secret in command", ClassifierDecision.SOFT_DENY),
    # Process/system
    (r"kill\s+-9\s+1\b", "Killing PID 1 (init)", ClassifierDecision.HARD_DENY),
    (r"pkill\s+-9", "Force-killing processes", ClassifierDecision.SOFT_DENY),
]

# Patterns that indicate safe operations — always allow
_SAFE_PATTERNS: list[tuple[str, str]] = [
    (r"^(ls|pwd|echo|date|whoami|hostname|uname)\b", "Basic read-only system command"),
    (r"^git\s+(status|log|diff|branch|show|remote|stash\s+list)", "Git read-only command"),
    (r"^(cat|head|tail|wc|sort|uniq|cut|tr)\b", "Text processing read-only command"),
    (r"^(python|node|ruby|go)\s+.*--version", "Version check"),
    (r"^(pytest|npm\s+test|cargo\s+test|go\s+test|make\s+test)", "Running tests"),
    (r"^(ruff|eslint|pylint|mypy|ty)\b", "Linting/type checking"),
]


class PermissionClassifier:
    """Two-stage permission classifier.

    Stage 1 uses deterministic regex patterns for fast classification.
    Stage 2 optionally calls an LLM for ambiguous cases.

    Args:
        llm_classifier: Optional async callable that takes tool_name and args,
            returns a ClassifierResult. Used for Stage 2 when Stage 1 is
            inconclusive.
    """

    def __init__(
        self,
        llm_classifier: Callable[[str, dict[str, Any]], ClassifierResult] | None = None,
    ) -> None:
        self._llm_classifier = llm_classifier

    def classify(self, tool_name: str, args: dict[str, Any]) -> ClassifierResult:
        """Classify a tool call through the two-stage pipeline.

        Args:
            tool_name: Name of the tool being called.
            args: Tool call arguments.

        Returns:
            Classification result with decision, reason, and stage.
        """
        # Stage 1: Deterministic regex classification
        stage1 = self._stage1_deterministic(tool_name, args)
        if stage1 is not None:
            return stage1

        # Stage 2: LLM fallback (if configured)
        if self._llm_classifier is not None:
            return self._llm_classifier(tool_name, args)

        # No LLM configured — inconclusive results route to user
        return ClassifierResult(
            decision=ClassifierDecision.SOFT_DENY,
            reason="No deterministic rule matched and no classifier model configured. Routing to user.",
            stage=1,
        )

    def _stage1_deterministic(
        self, tool_name: str, args: dict[str, Any],
    ) -> ClassifierResult | None:
        """Apply deterministic regex patterns.

        Args:
            tool_name: Name of the tool.
            args: Tool call arguments.

        Returns:
            Result if a pattern matched, None if inconclusive.
        """
        # Build searchable strings — both with and without tool name prefix
        args_str = _args_to_string(args)
        full_str = f"{tool_name} {args_str}"

        # Check dangerous patterns against both representations
        for pattern, reason, decision in _DANGEROUS_PATTERNS:
            if re.search(pattern, args_str, re.IGNORECASE) or re.search(pattern, full_str, re.IGNORECASE):
                return ClassifierResult(
                    decision=decision,
                    reason=reason,
                    stage=1,
                )

        # Check safe patterns against args only (patterns assume command is first word)
        for pattern, reason in _SAFE_PATTERNS:
            if re.search(pattern, args_str, re.IGNORECASE):
                return ClassifierResult(
                    decision=ClassifierDecision.ALLOW,
                    reason=reason,
                    stage=1,
                )

        # Inconclusive
        return None


def _args_to_string(args: dict[str, Any]) -> str:
    """Convert tool arguments to a searchable string.

    Args:
        args: Tool call arguments.

    Returns:
        Concatenated string of argument values.
    """
    parts = []
    for value in args.values():
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.append(str(value))
    return " ".join(parts)
