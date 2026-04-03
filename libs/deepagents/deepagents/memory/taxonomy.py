"""Memory taxonomy: categories, validation, and entry structure.

Defines the four memory categories (USER, FEEDBACK, PROJECT, REFERENCE),
validation rules that reject code facts, and the MemoryEntry dataclass
for structured memory storage.

Design principle: memory stores preferences, never code facts.
Code is read in real-time; memory prevents stale hallucinations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class MemoryCategory(Enum):
    """Categories for structured memory entries.

    Each category defines when to save and how to use the stored information.
    """

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"

    @property
    def save_trigger(self) -> str:
        """Describe when entries in this category should be saved.

        Returns:
            Human-readable description of the save trigger.
        """
        triggers = {
            MemoryCategory.USER: (
                "Save when the user shares personal preferences, "
                "communication style, identity info, or tool credentials "
                "(never passwords/tokens)."
            ),
            MemoryCategory.FEEDBACK: (
                "Save when the user corrects agent behavior, expresses "
                "dissatisfaction, or provides explicit instructions on "
                "how to do things differently."
            ),
            MemoryCategory.PROJECT: (
                "Save when the user describes project conventions, "
                "architectural decisions, team workflows, or deployment "
                "patterns that apply broadly."
            ),
            MemoryCategory.REFERENCE: (
                "Save when the user points to documentation, tools, APIs, "
                "or external resources that should be consulted in future "
                "tasks."
            ),
        }
        return triggers[self]

    @property
    def use_rule(self) -> str:
        """Describe how entries in this category should be consumed.

        Returns:
            Human-readable description of how to use this category.
        """
        rules = {
            MemoryCategory.USER: (
                "Apply passively to personalize responses. Do not repeat "
                "back unless relevant."
            ),
            MemoryCategory.FEEDBACK: (
                "Check before generating responses in the same domain. "
                "Treat as strong constraints."
            ),
            MemoryCategory.PROJECT: (
                "Consult when working on the associated project. Verify "
                "against live code before acting."
            ),
            MemoryCategory.REFERENCE: (
                "Look up when the topic arises. Always verify the "
                "resource is still current."
            ),
        }
        return rules[self]


# Patterns that indicate code facts rather than preferences or behaviors.
# These match function definitions, file paths with extensions, and
# class/method references like `MyClass.my_method`.
_CODE_FACT_PATTERNS: list[re.Pattern[str]] = [
    # Function/method definitions: def foo_bar(, async def baz(
    re.compile(r"\b(?:async\s+)?def\s+\w+\s*\("),
    # Absolute or relative file paths with common code extensions
    re.compile(
        r"(?:^|[\s\"'])(?:/|\./)[\w/\-]+\."
        r"(?:py|js|ts|tsx|jsx|rs|go|java|rb|c|cpp|h|hpp|css|scss|html)"
        r"\b"
    ),
    # Class.method references like MyClass.some_method
    re.compile(r"\b[A-Z]\w+\.\w+\("),
    # Import statements
    re.compile(r"\b(?:from|import)\s+[\w.]+"),
]

# Phrases that signal the entry is a behavioral hint rather than a code fact.
# If any of these appear alongside a code-fact pattern, the entry is allowed
# because it uses code as a reference pointer, not as a factual claim.
_HINT_PHRASES: list[str] = [
    "prefers",
    "prefers to",
    "likes to",
    "wants",
    "always",
    "never",
    "instead of",
    "rather than",
    "convention",
    "style",
    "pattern",
    "approach",
    "look at",
    "check",
    "refer to",
    "see also",
]


def validate_not_code_fact(content: str) -> list[str]:
    """Check whether content contains code facts that should not be memorized.

    Memory should store preferences, behaviors, and pointers -- not code
    facts like function signatures or file path assertions. A code reference
    is acceptable if it appears alongside a behavioral hint phrase (e.g.,
    "prefers the pattern in /src/utils.py").

    Args:
        content: The memory content to validate.

    Returns:
        List of validation error strings. Empty list means valid.
    """
    errors: list[str] = []
    lower = content.lower()

    has_hint = any(phrase in lower for phrase in _HINT_PHRASES)

    for pattern in _CODE_FACT_PATTERNS:
        if pattern.search(content) and not has_hint:
            errors.append(
                f"Content looks like a code fact (matched {pattern.pattern!r}). "
                "Memory should store preferences and pointers, not code facts. "
                "Add behavioral context or rephrase as a hint."
            )
            break  # One error is enough to reject

    return errors


@dataclass
class MemoryEntry:
    """A single structured memory entry.

    Attributes:
        name: Short identifier for the memory (used in index).
        description: One-line summary, max ~150 chars (shown in MEMORY.md index).
        category: Which of the four memory categories this belongs to.
        content: Full memory content (stored in topic file, not index).
        hint: When True, this memory should be verified before acting on it.
        created_at: UTC timestamp when the entry was first created.
        updated_at: UTC timestamp of the last modification.
    """

    name: str
    description: str
    category: MemoryCategory
    content: str
    hint: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def validate(self) -> list[str]:
        """Validate this entry against memory rules.

        Returns:
            List of validation error strings. Empty list means valid.
        """
        errors: list[str] = []

        if not self.name.strip():
            errors.append("Entry name must not be empty.")

        max_desc_length = 200
        if len(self.description) > max_desc_length:
            errors.append(
                f"Description is {len(self.description)} chars; "
                "keep it under 200 for the index."
            )

        if not self.content.strip():
            errors.append("Entry content must not be empty.")

        errors.extend(validate_not_code_fact(self.content))

        return errors

    def to_index_line(self) -> str:
        """Format this entry as a single line for the MEMORY.md index.

        Returns:
            Markdown-formatted index line with category tag.
        """
        tag = f"[{self.category.value}]"
        hint_marker = " (hint)" if self.hint else ""
        return f"- {tag} **{self.name}**: {self.description}{hint_marker}"

    def to_yaml_frontmatter(self) -> str:
        """Serialize entry metadata as YAML frontmatter.

        Returns:
            YAML frontmatter string including delimiters.
        """
        lines = [
            "---",
            f"name: {self.name}",
            f"description: {self.description}",
            f"category: {self.category.value}",
            f"hint: {str(self.hint).lower()}",
            f"created_at: {self.created_at.isoformat()}",
            f"updated_at: {self.updated_at.isoformat()}",
            "---",
        ]
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Serialize the full entry as a markdown file with YAML frontmatter.

        Returns:
            Complete markdown string ready to write to a topic file.
        """
        return f"{self.to_yaml_frontmatter()}\n\n{self.content}\n"

    @classmethod
    def from_markdown(cls, text: str) -> MemoryEntry:
        """Parse a memory entry from markdown with YAML frontmatter.

        Args:
            text: Markdown string with YAML frontmatter delimited by `---`.

        Returns:
            Parsed MemoryEntry instance.

        Raises:
            ValueError: If frontmatter is missing or malformed.
        """
        parts = text.split("---", maxsplit=2)
        if len(parts) < 3:  # noqa: PLR2004  # frontmatter requires 3 parts
            msg = "Memory file must have YAML frontmatter delimited by ---"
            raise ValueError(msg)

        frontmatter = parts[1].strip()
        body = parts[2].strip()

        meta: dict[str, str] = {}
        for line in frontmatter.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()

        required = {"name", "description", "category"}
        missing = required - set(meta)
        if missing:
            msg = f"Missing required frontmatter fields: {', '.join(sorted(missing))}"
            raise ValueError(msg)

        created = (
            datetime.fromisoformat(meta["created_at"])
            if "created_at" in meta
            else datetime.now(UTC)
        )
        updated = (
            datetime.fromisoformat(meta["updated_at"])
            if "updated_at" in meta
            else datetime.now(UTC)
        )

        return cls(
            name=meta["name"],
            description=meta["description"],
            category=MemoryCategory(meta["category"]),
            content=body,
            hint=meta.get("hint", "true").lower() == "true",
            created_at=created,
            updated_at=updated,
        )
