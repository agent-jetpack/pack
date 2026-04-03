"""Structured memory middleware -- injects memories and extracts new ones.

Loads MEMORY.md index from ~/.pack/memories/ and injects relevant entries
into the system prompt before model calls. After responses, runs the
MemoryExtractor at a rate-limited interval (max 1 per 3 turns) to capture
preferences and corrections.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage

from deepagents.memory.extractor import MemoryExtractor
from deepagents.memory.index import MemoryIndex

logger = logging.getLogger(__name__)


class PackMemoryMiddleware(AgentMiddleware):
    """Inject structured memories and extract new ones from conversation.

    Before each model call, loads the MEMORY.md index and injects a
    compact summary of relevant entries as a system message. After
    the response, checks whether extraction is due and runs the
    extractor to capture new memories.

    Args:
        index: The MemoryIndex for reading/writing memories.
        extractor: The MemoryExtractor for post-response extraction.
    """

    def __init__(
        self,
        index: MemoryIndex,
        extractor: MemoryExtractor | None = None,
    ) -> None:
        self._index = index
        self._extractor = extractor

    @property
    def index(self) -> MemoryIndex:
        """Access the memory index."""
        return self._index

    @property
    def extractor(self) -> MemoryExtractor | None:
        """Access the memory extractor, if configured."""
        return self._extractor

    async def awrap_model_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """Inject memories before model call and extract after response.

        Args:
            request: The model request being sent.
            handler: Async callback that executes the model request.

        Returns:
            The model response, unmodified.
        """
        # Inject memory context before the call
        try:
            self._inject_memories(request)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to inject memories", exc_info=True)

        response = await handler(request)

        # Extract memories after the call (rate-limited)
        if self._extractor is not None:
            try:
                conversation = _extract_recent_conversation(request)
                if conversation:
                    self._extractor.on_turn(conversation)
            except Exception:  # noqa: BLE001
                logger.debug("Memory extraction failed", exc_info=True)

        return response

    def _inject_memories(self, request: Any) -> None:
        """Load index and prepend a memory summary to messages.

        Only injects if the index file exists and has content.

        Args:
            request: The model request whose state.messages will be modified.
        """
        messages = _get_messages(request)
        if messages is None:
            return

        lines = self._index.load_index()
        if not lines:
            return

        # Build a compact memory block from the index lines
        content_lines = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
        if not content_lines:
            return

        memory_block = (
            "## Memories (from previous sessions)\n\n"
            + "\n".join(content_lines)
            + "\n\nThese are hints, not facts. Verify against live code before acting."
        )

        # Insert as the second message (after system prompt, before user messages)
        insert_pos = 1 if messages and isinstance(messages[0], SystemMessage) else 0
        messages.insert(insert_pos, SystemMessage(content=memory_block))


def _get_messages(request: Any) -> list[AnyMessage] | None:
    """Extract the mutable messages list from a model request.

    Args:
        request: The model request object.

    Returns:
        The messages list, or None if not accessible.
    """
    if hasattr(request, "state") and hasattr(request.state, "messages"):
        return request.state.messages
    return None


def _extract_recent_conversation(request: Any) -> str:
    """Build a conversation string from the last few messages for extraction.

    Args:
        request: The model request containing conversation state.

    Returns:
        A formatted string of recent conversation, or empty string.
    """
    messages = _get_messages(request)
    if not messages:
        return ""

    recent_count = 6
    recent = messages[-recent_count:]
    parts: list[str] = []
    for msg in recent:
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        parts.append(f"{role}: {content}")

    return "\n".join(parts)
