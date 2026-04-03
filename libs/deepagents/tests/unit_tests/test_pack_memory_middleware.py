"""Tests for PackMemoryMiddleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import HumanMessage, SystemMessage

from deepagents.memory.index import MemoryIndex
from deepagents.middleware.pack.memory_middleware import (
    PackMemoryMiddleware,
    _extract_recent_conversation,
    _get_messages,
)


class TestPackMemoryMiddleware:
    async def test_passes_through_when_no_index(self, tmp_path: str) -> None:
        index = MemoryIndex(str(tmp_path / "memories"))
        middleware = PackMemoryMiddleware(index)

        request = MagicMock()
        request.state = MagicMock()
        request.state.messages = [HumanMessage(content="hello")]
        handler = AsyncMock(return_value="response")

        result = await middleware.awrap_model_call(request, handler)
        assert result == "response"
        handler.assert_called_once()

    async def test_injects_memory_block(self, tmp_path: str) -> None:
        mem_dir = tmp_path / "memories"
        index = MemoryIndex(str(mem_dir))
        index.ensure_dirs()

        # Write a simple MEMORY.md index
        index_path = mem_dir / "MEMORY.md"
        index_path.write_text(
            "# Memory Index\n\n"
            "## User\n\n"
            "- [user] **editor_pref**: Prefers vim keybindings (hint)\n"
        )

        middleware = PackMemoryMiddleware(index)

        messages: list = [
            SystemMessage(content="You are a helpful agent."),
            HumanMessage(content="hello"),
        ]
        request = MagicMock()
        request.state = MagicMock()
        request.state.messages = messages
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)

        # A SystemMessage with memory content should be injected at position 1
        assert len(messages) == 3  # noqa: PLR2004
        assert isinstance(messages[1], SystemMessage)
        assert "editor_pref" in messages[1].content
        assert "hints, not facts" in messages[1].content

    async def test_no_injection_when_index_empty(self, tmp_path: str) -> None:
        mem_dir = tmp_path / "memories"
        index = MemoryIndex(str(mem_dir))
        index.ensure_dirs()

        # Write an empty MEMORY.md
        index_path = mem_dir / "MEMORY.md"
        index_path.write_text("# Memory Index\n")

        middleware = PackMemoryMiddleware(index)

        messages: list = [HumanMessage(content="hello")]
        request = MagicMock()
        request.state = MagicMock()
        request.state.messages = messages
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)

        # No injection -- still just the original message
        assert len(messages) == 1

    async def test_extractor_called_when_provided(self, tmp_path: str) -> None:
        index = MemoryIndex(str(tmp_path / "memories"))
        extractor = MagicMock()
        extractor.on_turn = MagicMock(return_value=[])

        middleware = PackMemoryMiddleware(index, extractor=extractor)

        messages = [HumanMessage(content="I prefer dark mode")]
        request = MagicMock()
        request.state = MagicMock()
        request.state.messages = messages
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)
        extractor.on_turn.assert_called_once()


class TestHelpers:
    def test_get_messages_returns_list(self) -> None:
        request = MagicMock()
        request.state = MagicMock()
        request.state.messages = [HumanMessage(content="test")]
        result = _get_messages(request)
        assert result is not None
        assert len(result) == 1

    def test_get_messages_returns_none_without_state(self) -> None:
        request = MagicMock(spec=[])
        assert _get_messages(request) is None

    def test_extract_recent_conversation(self) -> None:
        request = MagicMock()
        request.state = MagicMock()
        request.state.messages = [
            HumanMessage(content="hello"),
            HumanMessage(content="world"),
        ]
        result = _extract_recent_conversation(request)
        assert "hello" in result
        assert "world" in result

    def test_extract_recent_conversation_empty(self) -> None:
        request = MagicMock(spec=[])
        assert _extract_recent_conversation(request) == ""
