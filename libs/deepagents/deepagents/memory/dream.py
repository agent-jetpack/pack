"""Dream consolidator: offline memory consolidation from session transcripts.

Reads session transcripts from the last 24 hours, identifies patterns
(repeated corrections, preference signals, recurring topics), and
consolidates findings into structured memory entries.

The consolidator has restricted tool access: it can only read and write
memory files, never execute code or access external systems.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from deepagents.memory.taxonomy import MemoryCategory, MemoryEntry, validate_not_code_fact

if TYPE_CHECKING:
    from deepagents.memory.index import MemoryIndex

logger = logging.getLogger(__name__)

_DEFAULT_TRANSCRIPT_DIR = "~/.pack/transcripts"
_MAX_TRANSCRIPT_AGE_HOURS = 24

CONSOLIDATION_PROMPT = """You are a memory consolidation agent. Analyze these session transcripts
from the last 24 hours and identify patterns worth remembering.

Look for:
1. REPEATED CORRECTIONS: The user corrected the agent on the same thing multiple times.
2. PREFERENCE SIGNALS: Consistent choices the user made (style, tools, approaches).
3. PROJECT PATTERNS: Recurring architectural or workflow themes.
4. RESOURCE REFERENCES: Documentation or tools the user kept pointing to.

Rules:
- Extract PATTERNS, not individual events.
- Do NOT store code facts (function signatures, exact file paths as truth).
- Pointers to where to look are OK ("check the utils module for helpers").
- Each entry is a HINT that should be verified before acting.
- Skip anything already covered by existing memories (listed below).

Existing memories:
{existing_memories}

Transcripts:
{transcripts}

Respond with a JSON array of objects:
  {{"name": "short_id", "description": "~150 char summary", "category": "user|feedback|project|reference", "content": "full detail"}}

If no new patterns found, respond with: []"""


class ConsolidationModel(Protocol):
    """Protocol for the callable that performs LLM-based consolidation.

    Accepts a prompt string and returns the model's response string.
    """

    def __call__(self, prompt: str) -> str:
        """Call the model with a prompt.

        Args:
            prompt: The consolidation prompt.

        Returns:
            The model's response as a string (expected to be JSON).
        """
        ...


class DreamConsolidator:
    """Offline memory consolidation from session transcripts.

    Reads recent transcripts, identifies patterns, and creates new memory
    entries. Designed to run during idle periods (the "dream" metaphor).

    This class has restricted capabilities by design: it only reads
    transcripts and writes to the memory index. It does not execute code,
    access the network, or modify project files.

    Args:
        index: The MemoryIndex to store consolidated entries.
        model: Callable that accepts a prompt string and returns the response.
        transcript_dir: Directory containing session transcript files.
        max_age_hours: Only process transcripts newer than this many hours.
    """

    def __init__(
        self,
        index: MemoryIndex,
        model: ConsolidationModel,
        *,
        transcript_dir: str = _DEFAULT_TRANSCRIPT_DIR,
        max_age_hours: int = _MAX_TRANSCRIPT_AGE_HOURS,
    ) -> None:
        """Initialize the dream consolidator.

        Args:
            index: The MemoryIndex to store consolidated entries.
            model: Callable that accepts a prompt and returns the response.
            transcript_dir: Directory containing session transcript files.
            max_age_hours: Only process transcripts newer than this many hours.
        """
        self._index = index
        self._model = model
        self._transcript_dir = Path(transcript_dir).expanduser()
        self._max_age_hours = max_age_hours

    @property
    def transcript_dir(self) -> Path:
        """Directory where session transcripts are stored."""
        return self._transcript_dir

    def find_recent_transcripts(self) -> list[Path]:
        """Find transcript files modified within the configured time window.

        Returns:
            List of paths to recent transcript files, sorted oldest first.
        """
        if not self._transcript_dir.exists():
            return []

        now = datetime.now(UTC)
        cutoff_seconds = self._max_age_hours * 3600
        recent: list[tuple[float, Path]] = []

        for path in self._transcript_dir.glob("*.md"):
            try:
                mtime = path.stat().st_mtime
                age = now.timestamp() - mtime
                if age <= cutoff_seconds:
                    recent.append((mtime, path))
            except OSError:
                logger.warning("Could not stat transcript file: %s", path)

        # Sort oldest first
        recent.sort(key=lambda x: x[0])
        return [path for _, path in recent]

    def read_transcripts(self, paths: list[Path]) -> str:
        """Read and concatenate transcript files.

        Args:
            paths: List of transcript file paths to read.

        Returns:
            Concatenated transcript text with file separators.
        """
        sections: list[str] = []
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
                sections.append(f"--- {path.name} ---\n{text}")
            except OSError:
                logger.warning("Could not read transcript: %s", path)

        return "\n\n".join(sections)

    def consolidate(self) -> list[MemoryEntry]:
        """Run the full consolidation pipeline.

        Finds recent transcripts, reads them, sends to the model for
        pattern extraction, and stores valid entries.

        Returns:
            List of newly created MemoryEntry objects.
        """
        paths = self.find_recent_transcripts()
        if not paths:
            logger.info("No recent transcripts found for consolidation")
            return []

        transcripts = self.read_transcripts(paths)
        if not transcripts.strip():
            return []

        # Build existing memories summary for dedup
        existing = self._index.list_entries()
        existing_summary = "\n".join(
            f"- {e.name}: {e.description}" for e in existing
        )
        if not existing_summary:
            existing_summary = "(none)"

        prompt = CONSOLIDATION_PROMPT.format(
            existing_memories=existing_summary,
            transcripts=transcripts,
        )

        try:
            response = self._model(prompt)
        except Exception:
            logger.exception("Consolidation model call failed")
            return []

        return self._parse_and_store(response)

    def _parse_and_store(self, response: str) -> list[MemoryEntry]:
        """Parse consolidation model response and store valid entries.

        Args:
            response: JSON string from the consolidation model.

        Returns:
            List of successfully stored MemoryEntry objects.
        """
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse consolidation response as JSON")
            return []

        if not isinstance(items, list):
            logger.warning("Consolidation response is not a JSON array")
            return []

        stored: list[MemoryEntry] = []
        now = datetime.now(UTC)

        for item in items:
            if not isinstance(item, dict):
                continue

            name = item.get("name", "").strip()
            description = item.get("description", "").strip()
            category_str = item.get("category", "").strip()
            content = item.get("content", "").strip()

            if not all([name, description, category_str, content]):
                continue

            try:
                category = MemoryCategory(category_str)
            except ValueError:
                logger.warning("Unknown memory category: %s", category_str)
                continue

            code_errors = validate_not_code_fact(content)
            if code_errors:
                logger.info(
                    "Rejected code fact from consolidation: %s -- %s",
                    name,
                    code_errors[0],
                )
                continue

            entry = MemoryEntry(
                name=name,
                description=description,
                category=category,
                content=content,
                hint=True,
                created_at=now,
                updated_at=now,
            )

            errors = self._index.add(entry)
            if errors:
                logger.info(
                    "Failed to store consolidated memory '%s': %s",
                    name,
                    "; ".join(errors),
                )
            else:
                stored.append(entry)

        return stored
