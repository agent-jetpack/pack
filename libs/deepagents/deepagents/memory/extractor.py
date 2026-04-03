"""Memory extractor: post-response hook for extracting memory-worthy information.

Analyzes conversation turns and extracts preferences, corrections, and
project context into structured memory entries. Uses an auxiliary (cheap)
model for extraction to minimize cost.

Rate-limited to max 1 extraction per 3 turns to avoid noise.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from deepagents.memory.taxonomy import MemoryCategory, MemoryEntry, validate_not_code_fact

if TYPE_CHECKING:
    from deepagents.memory.index import MemoryIndex

logger = logging.getLogger(__name__)

_EXTRACTION_INTERVAL = 3  # Minimum turns between extractions

# Prompt sent to the auxiliary model for memory extraction.
EXTRACTION_PROMPT = """Analyze this conversation segment and extract memory-worthy information.

Rules:
- Extract PREFERENCES, CORRECTIONS, PROJECT CONVENTIONS, and RESOURCE POINTERS.
- Do NOT extract code facts (function names, file paths as factual claims).
- Code references are OK only as pointers ("check /src/utils.py for the pattern").
- Each extraction must be a hint, not an assertion of truth.
- Skip transient info (one-time tasks, greetings, acknowledgments).

Categories:
- user: Personal preferences, communication style, identity, tool credentials (never passwords).
- feedback: Corrections to agent behavior, "do this instead", quality signals.
- project: Conventions, architecture decisions, team workflows, deployment patterns.
- reference: Documentation, tools, APIs, external resources to consult.

Respond with one JSON array of objects, each with:
  {{"name": "short_id", "description": "~150 char summary", "category": "user|feedback|project|reference", "content": "full detail"}}

If nothing is memory-worthy, respond with an empty array: []

Conversation:
{conversation}"""


class ExtractionModel(Protocol):
    """Protocol for the callable that performs LLM-based extraction.

    Accepts a prompt string and returns the model's response string.
    This allows the extractor to remain model-agnostic.
    """

    def __call__(self, prompt: str) -> str:
        """Call the model with a prompt.

        Args:
            prompt: The extraction prompt.

        Returns:
            The model's response as a string (expected to be JSON).
        """
        ...


class MemoryExtractor:
    """Post-response hook that extracts memory-worthy information from conversation.

    Designed to be called after each agent response. Rate-limited to avoid
    excessive extraction on every turn. Uses a cheap auxiliary model to
    classify and extract information into the four memory categories.

    Args:
        index: The MemoryIndex to store extracted entries.
        model: Callable that accepts a prompt string and returns the model response.
        interval: Minimum number of turns between extraction attempts.
    """

    def __init__(
        self,
        index: MemoryIndex,
        model: ExtractionModel,
        *,
        interval: int = _EXTRACTION_INTERVAL,
    ) -> None:
        """Initialize the memory extractor.

        Args:
            index: The MemoryIndex to store extracted entries.
            model: Callable that accepts a prompt and returns the model response.
            interval: Minimum number of turns between extraction attempts.
        """
        self._index = index
        self._model = model
        self._interval = interval
        self._turn_count = 0
        self._last_extraction_turn = -interval  # Allow first extraction immediately

    @property
    def turn_count(self) -> int:
        """Number of turns observed since instantiation."""
        return self._turn_count

    def should_extract(self) -> bool:
        """Check whether extraction should run on this turn.

        Returns:
            True if enough turns have passed since the last extraction.
        """
        return (self._turn_count - self._last_extraction_turn) >= self._interval

    def on_turn(self, conversation: str) -> list[MemoryEntry]:
        """Process a conversation turn and extract memories if due.

        Call this after each agent response with the recent conversation
        context. Extraction only runs if the rate limit allows it.

        Args:
            conversation: Recent conversation text to analyze.

        Returns:
            List of newly created MemoryEntry objects. Empty if extraction
            was skipped or nothing was memory-worthy.
        """
        self._turn_count += 1

        if not self.should_extract():
            return []

        self._last_extraction_turn = self._turn_count
        return self._extract(conversation)

    def _extract(self, conversation: str) -> list[MemoryEntry]:
        """Run extraction using the auxiliary model.

        Args:
            conversation: Conversation text to analyze.

        Returns:
            List of newly created and stored MemoryEntry objects.
        """
        prompt = EXTRACTION_PROMPT.format(conversation=conversation)

        try:
            response = self._model(prompt)
        except Exception:
            logger.exception("Memory extraction model call failed")
            return []

        return self._parse_and_store(response)

    def _parse_and_store(self, response: str) -> list[MemoryEntry]:
        """Parse model response and store valid entries.

        Args:
            response: JSON string from the extraction model.

        Returns:
            List of successfully stored MemoryEntry objects.
        """
        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first and last fence lines
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse extraction response as JSON")
            return []

        if not isinstance(items, list):
            logger.warning("Extraction response is not a JSON array")
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

            # Validate category
            try:
                category = MemoryCategory(category_str)
            except ValueError:
                logger.warning("Unknown memory category: %s", category_str)
                continue

            # Validate not a code fact
            code_errors = validate_not_code_fact(content)
            if code_errors:
                logger.info(
                    "Rejected code fact from extraction: %s -- %s",
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
                    "Failed to store extracted memory '%s': %s",
                    name,
                    "; ".join(errors),
                )
            else:
                stored.append(entry)

        return stored
