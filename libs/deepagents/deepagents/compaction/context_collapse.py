"""Context collapse for verbose tool results.

Replaces large tool results with concise summaries while persisting
the original content to disk for later re-expansion via `/expand`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ContextCollapseEntry:
    """A collapsed tool result with a pointer to the full content.

    Args:
        entry_id: Unique identifier for re-expansion.
        tool_name: Name of the tool that produced the result.
        summary: Concise summary replacing the full content.
        original_path: File path where original content is stored.
        token_count: Approximate tokens in the original content.
        created_at: When the collapse occurred.
    """

    entry_id: str
    tool_name: str
    summary: str
    original_path: str
    token_count: int
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


class ContextCollapser:
    """Collapses verbose tool results and manages re-expansion.

    Args:
        storage_dir: Directory for persisting collapsed content.
        threshold: Token count above which a tool result is collapsed.
    """

    def __init__(
        self,
        storage_dir: Path | str,
        *,
        threshold: int = 5000,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._threshold = threshold
        self._entries: dict[str, ContextCollapseEntry] = {}

    @property
    def threshold(self) -> int:
        """Token count threshold for collapsing."""
        return self._threshold

    @property
    def entries(self) -> dict[str, ContextCollapseEntry]:
        """All active collapse entries keyed by ID."""
        return self._entries

    def should_collapse(self, content: str) -> bool:
        """Check if content exceeds the collapse threshold.

        Args:
            content: Tool result content to evaluate.

        Returns:
            True if the content should be collapsed.
        """
        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(content) // 4
        return estimated_tokens > self._threshold

    def collapse(self, tool_name: str, content: str, summary: str) -> ContextCollapseEntry:
        """Collapse a tool result, persisting the original.

        Args:
            tool_name: Name of the tool that produced the result.
            content: Full original content to persist.
            summary: Concise summary to replace the content.

        Returns:
            Collapse entry with metadata for the replacement.
        """
        entry_id = str(uuid.uuid4())[:8]
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        original_path = self._storage_dir / f"{entry_id}.txt"
        original_path.write_text(content)

        estimated_tokens = len(content) // 4
        entry = ContextCollapseEntry(
            entry_id=entry_id,
            tool_name=tool_name,
            summary=summary,
            original_path=str(original_path),
            token_count=estimated_tokens,
        )
        self._entries[entry_id] = entry
        return entry

    def expand(self, entry_id: str) -> str | None:
        """Re-expand a collapsed result by reading the persisted original.

        Args:
            entry_id: ID of the collapse entry to expand.

        Returns:
            Original content if found, None if the entry doesn't exist.
        """
        entry = self._entries.get(entry_id)
        if entry is None:
            return None

        path = Path(entry.original_path)
        if not path.exists():
            return None

        return path.read_text()

    def format_collapsed(self, entry: ContextCollapseEntry) -> str:
        """Format a collapsed entry as a replacement tool result.

        Args:
            entry: The collapse entry to format.

        Returns:
            Formatted string with summary and expansion hint.
        """
        return (
            f"[Collapsed: ~{entry.token_count} tokens from {entry.tool_name}]\n"
            f"{entry.summary}\n"
            f"[Use /expand {entry.entry_id} to see full content]"
        )

    def list_entries(self) -> list[ContextCollapseEntry]:
        """List all active collapse entries.

        Returns:
            List of entries sorted by creation time.
        """
        return sorted(self._entries.values(), key=lambda e: e.created_at)
