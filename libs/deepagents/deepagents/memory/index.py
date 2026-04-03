"""Memory index: 3-layer storage with on-demand topic loading.

Manages MEMORY.md as a lightweight index (~150-char pointers per entry)
with on-demand loading of full topic files. The three layers are:

1. Index (MEMORY.md) -- always loaded, contains one-line pointers.
2. Topic files -- loaded on demand when an index entry matches a query.
3. Transcripts -- grep-only, never fully loaded.

The index is kept under 200 lines to stay context-efficient.
"""

from __future__ import annotations

import logging
from pathlib import Path

from deepagents.memory.taxonomy import MemoryCategory, MemoryEntry

logger = logging.getLogger(__name__)

_MAX_INDEX_LINES = 200
_DEFAULT_MEMORY_DIR = "~/.pack/memories"


class MemoryIndex:
    """Manages structured memory with a compact index and on-demand topic files.

    The index file (MEMORY.md) contains short pointers. Full content lives in
    category subdirectories as individual markdown files with YAML frontmatter.

    Directory structure:
        ~/.pack/memories/
            MEMORY.md           # index file
            user/               # USER category topic files
            feedback/           # FEEDBACK category topic files
            project/            # PROJECT category topic files
            reference/          # REFERENCE category topic files

    Args:
        memory_dir: Root directory for memory storage.
    """

    def __init__(self, memory_dir: str = _DEFAULT_MEMORY_DIR) -> None:
        """Initialize the memory index.

        Args:
            memory_dir: Root directory for memory storage.
        """
        self._root = Path(memory_dir).expanduser()
        self._index_path = self._root / "MEMORY.md"
        self._entries: dict[str, MemoryEntry] = {}
        self._index_loaded = False

    @property
    def root(self) -> Path:
        """Root directory for memory storage."""
        return self._root

    @property
    def index_path(self) -> Path:
        """Path to the MEMORY.md index file."""
        return self._index_path

    def _category_dir(self, category: MemoryCategory) -> Path:
        """Get the directory for a given category.

        Args:
            category: The memory category.

        Returns:
            Path to the category subdirectory.
        """
        return self._root / category.value

    def _topic_path(self, entry: MemoryEntry) -> Path:
        """Get the file path for a topic file.

        Args:
            entry: The memory entry.

        Returns:
            Path to the topic markdown file.
        """
        safe_name = entry.name.lower().replace(" ", "_")
        # Strip non-alphanumeric chars except underscore
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        return self._category_dir(entry.category) / f"{safe_name}.md"

    def ensure_dirs(self) -> None:
        """Create the memory directory structure if it does not exist."""
        self._root.mkdir(parents=True, exist_ok=True)
        for category in MemoryCategory:
            self._category_dir(category).mkdir(parents=True, exist_ok=True)

    def load_index(self) -> list[str]:
        """Load the MEMORY.md index file and return its lines.

        Does not load topic file contents -- those are loaded on demand
        via `load_topic`.

        Returns:
            List of lines from MEMORY.md. Empty list if file does not exist.
        """
        self._index_loaded = True

        if not self._index_path.exists():
            return []

        return self._index_path.read_text(encoding="utf-8").splitlines()

    def load_topic(self, name: str) -> MemoryEntry | None:
        """Load a full topic file by entry name.

        Searches across all category directories for a matching file.
        Caches the result in memory for subsequent access.

        Args:
            name: The entry name to look up.

        Returns:
            The parsed MemoryEntry, or None if not found.
        """
        # Check cache first
        if name in self._entries:
            return self._entries[name]

        safe_name = name.lower().replace(" ", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")

        for category in MemoryCategory:
            path = self._category_dir(category) / f"{safe_name}.md"
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8")
                    entry = MemoryEntry.from_markdown(text)
                except ValueError:
                    logger.warning("Failed to parse topic file: %s", path)
                    return None
                else:
                    self._entries[name] = entry
                    return entry

        return None

    def search(self, query: str) -> list[MemoryEntry]:
        """Search the index for entries matching a keyword query.

        Loads topic files on demand for matching index lines. The search
        is case-insensitive and matches against entry names and descriptions
        in the index.

        Args:
            query: Space-separated keywords to match against.

        Returns:
            List of matching MemoryEntry objects with full content loaded.
        """
        if not self._index_loaded:
            self.load_index()

        keywords = query.lower().split()
        if not keywords:
            return []

        matches: list[MemoryEntry] = []
        lines = self.load_index()

        for line in lines:
            lower_line = line.lower()
            if all(kw in lower_line for kw in keywords):
                # Extract name from index line format: - [category] **name**: desc
                name = _extract_name_from_index_line(line)
                if name:
                    entry = self.load_topic(name)
                    if entry is not None:
                        matches.append(entry)

        return matches

    def add(self, entry: MemoryEntry) -> list[str]:
        """Add a new memory entry to the index and write its topic file.

        Validates the entry, checks for duplicates, writes the topic file,
        and updates MEMORY.md.

        Args:
            entry: The memory entry to add.

        Returns:
            List of validation errors. Empty list means success.
        """
        errors = entry.validate()
        if errors:
            return errors

        # Check for duplicates
        if self._has_duplicate(entry.name):
            return [f"An entry named '{entry.name}' already exists."]

        # Check index line limit
        lines = self.load_index()
        # Count non-empty, non-header lines
        content_lines = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
        if len(content_lines) >= _MAX_INDEX_LINES:
            return [
                f"Index has {len(content_lines)} entries "
                f"(max {_MAX_INDEX_LINES}). "
                "Consolidate or remove entries before adding more."
            ]

        self.ensure_dirs()

        # Write topic file
        topic_path = self._topic_path(entry)
        topic_path.write_text(entry.to_markdown(), encoding="utf-8")

        # Update index
        self._entries[entry.name] = entry
        self._write_index()

        logger.info("Added memory entry: %s [%s]", entry.name, entry.category.value)
        return []

    def remove(self, name: str) -> bool:
        """Remove a memory entry by name.

        Deletes the topic file and removes the line from MEMORY.md.

        Args:
            name: The entry name to remove.

        Returns:
            True if the entry was found and removed.
        """
        entry = self.load_topic(name)
        if entry is None:
            return False

        # Delete topic file
        topic_path = self._topic_path(entry)
        if topic_path.exists():
            topic_path.unlink()

        # Remove from cache
        self._entries.pop(name, None)

        # Rewrite index
        self._write_index()

        logger.info("Removed memory entry: %s", name)
        return True

    def list_entries(
        self, *, category: MemoryCategory | None = None
    ) -> list[MemoryEntry]:
        """List all entries, optionally filtered by category.

        Loads topic files for all index entries. Use sparingly on large indices.

        Args:
            category: If provided, only return entries in this category.

        Returns:
            List of MemoryEntry objects.
        """
        self.load_index()
        result: list[MemoryEntry] = []

        for cat in MemoryCategory:
            if category is not None and cat != category:
                continue
            cat_dir = self._category_dir(cat)
            if not cat_dir.exists():
                continue
            for path in sorted(cat_dir.glob("*.md")):
                try:
                    text = path.read_text(encoding="utf-8")
                    entry = MemoryEntry.from_markdown(text)
                    self._entries[entry.name] = entry
                    if category is None or entry.category == category:
                        result.append(entry)
                except ValueError:
                    logger.warning("Failed to parse topic file: %s", path)

        return result

    def _has_duplicate(self, name: str) -> bool:
        """Check whether an entry with the given name already exists.

        Args:
            name: The entry name to check.

        Returns:
            True if a duplicate exists.
        """
        return self.load_topic(name) is not None

    def _write_index(self) -> None:
        """Rewrite MEMORY.md from the current set of cached entries."""
        self.ensure_dirs()

        # Collect all entries from topic files
        all_entries: list[MemoryEntry] = []
        for cat in MemoryCategory:
            cat_dir = self._category_dir(cat)
            if not cat_dir.exists():
                continue
            for path in sorted(cat_dir.glob("*.md")):
                try:
                    text = path.read_text(encoding="utf-8")
                    entry = MemoryEntry.from_markdown(text)
                    all_entries.append(entry)
                except ValueError:
                    logger.warning("Skipping malformed topic file: %s", path)

        lines = ["# Memory Index", ""]

        # Group by category
        for cat in MemoryCategory:
            cat_entries = [e for e in all_entries if e.category == cat]
            if not cat_entries:
                continue
            lines.append(f"## {cat.value.title()}")
            lines.append("")
            lines.extend(entry.to_index_line() for entry in cat_entries)
            lines.append("")

        self._index_path.write_text("\n".join(lines), encoding="utf-8")


def _extract_name_from_index_line(line: str) -> str | None:
    """Extract the entry name from a MEMORY.md index line.

    Expected format: `- [category] **name**: description`

    Args:
        line: A single line from MEMORY.md.

    Returns:
        The extracted name, or None if the line does not match.
    """
    # Match: - [category] **name**: description
    if "**" not in line:
        return None

    try:
        start = line.index("**") + 2
        end = line.index("**", start)
        return line[start:end]
    except ValueError:
        return None
