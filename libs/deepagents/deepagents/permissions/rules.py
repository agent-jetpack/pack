"""Cross-session permission rule storage.

Rules persist user decisions about tool calls so the same prompt
is not shown twice for the same operation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class RuleDecision(str, Enum):
    """Decision stored in a permission rule."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass
class PermissionRule:
    """A persisted permission decision for a tool call pattern.

    Args:
        tool_name: Exact tool name this rule applies to.
        pattern: Glob/regex pattern matching tool call arguments.
        decision: Whether to allow or deny matching calls.
        created_at: When the rule was created.
        hit_count: Number of times this rule has been matched.
    """

    tool_name: str
    pattern: str
    decision: RuleDecision
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    hit_count: int = 0

    def matches(self, tool_name: str, args_str: str) -> bool:
        """Check if this rule matches a tool call.

        Args:
            tool_name: Name of the tool being called.
            args_str: Serialized string of tool call arguments.

        Returns:
            True if this rule applies to the given tool call.
        """
        if self.tool_name != tool_name:
            return False
        try:
            return bool(re.search(self.pattern, args_str))
        except re.error:
            return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "tool_name": self.tool_name,
            "pattern": self.pattern,
            "decision": self.decision.value,
            "created_at": self.created_at.isoformat(),
            "hit_count": self.hit_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionRule:
        """Deserialize from dictionary.

        Args:
            data: Dictionary with rule fields.

        Returns:
            Reconstructed rule instance.
        """
        return cls(
            tool_name=data["tool_name"],
            pattern=data["pattern"],
            decision=RuleDecision(data["decision"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            hit_count=data.get("hit_count", 0),
        )


class RuleStore:
    """Persists permission rules to a JSON file.

    Rules are loaded lazily on first access and saved after each mutation.

    Args:
        path: File path for rule persistence.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._rules: list[PermissionRule] | None = None

    @property
    def rules(self) -> list[PermissionRule]:
        """Lazily load and return all rules."""
        if self._rules is None:
            self._load()
        return self._rules  # type: ignore[return-value]

    def match(self, tool_name: str, args: dict[str, Any]) -> PermissionRule | None:
        """Find the first rule matching a tool call.

        Args:
            tool_name: Name of the tool.
            args: Tool call arguments.

        Returns:
            Matching rule if found, None otherwise.
        """
        args_str = json.dumps(args, sort_keys=True)
        for rule in self.rules:
            if rule.matches(tool_name, args_str):
                rule.hit_count += 1
                self._save()
                return rule
        return None

    def add(self, rule: PermissionRule) -> None:
        """Add a new rule and persist.

        Args:
            rule: The permission rule to store.
        """
        self.rules.append(rule)
        self._save()

    def remove(self, tool_name: str, pattern: str) -> bool:
        """Remove a rule by tool name and pattern.

        Args:
            tool_name: Tool name to match.
            pattern: Pattern to match.

        Returns:
            True if a rule was removed.
        """
        before = len(self.rules)
        self._rules = [
            r for r in self.rules
            if not (r.tool_name == tool_name and r.pattern == pattern)
        ]
        if len(self._rules) < before:
            self._save()
            return True
        return False

    def clear(self) -> None:
        """Remove all rules."""
        self._rules = []
        self._save()

    def _load(self) -> None:
        """Load rules from disk."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._rules = [PermissionRule.from_dict(r) for r in data]
            except (json.JSONDecodeError, KeyError):
                self._rules = []
        else:
            self._rules = []

    def _save(self) -> None:
        """Persist rules to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([r.to_dict() for r in (self._rules or [])], indent=2)
        )
