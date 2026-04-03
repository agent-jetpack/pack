"""Tests for the multi-strategy context compaction system."""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deepagents.compaction.context_collapse import ContextCollapser
from deepagents.compaction.monitor import CompactionMonitor, CompactionTier
from deepagents.compaction.segment_protocol import SegmentProtocol


class TestCompactionMonitor:
    def test_none_when_under_threshold(self) -> None:
        monitor = CompactionMonitor(context_window=1000, trim_threshold=0.7)
        messages = [HumanMessage(content="short message")]
        assert monitor.check(messages) == CompactionTier.NONE

    def test_trim_at_70_percent(self) -> None:
        monitor = CompactionMonitor(context_window=100, trim_threshold=0.7)
        # ~80 tokens of content should trigger trim
        messages = [HumanMessage(content="word " * 80)]
        tier = monitor.check(messages)
        assert tier in (CompactionTier.TRIM, CompactionTier.COLLAPSE, CompactionTier.SUMMARIZE)

    def test_summarize_at_90_percent(self) -> None:
        monitor = CompactionMonitor(context_window=100, summarize_threshold=0.9)
        messages = [HumanMessage(content="word " * 200)]
        assert monitor.check(messages) == CompactionTier.SUMMARIZE

    def test_token_count(self) -> None:
        monitor = CompactionMonitor()
        messages = [HumanMessage(content="hello world")]
        count = monitor.token_count(messages)
        assert count > 0


class TestContextCollapser:
    def test_should_collapse_large_content(self, tmp_path: Path) -> None:
        collapser = ContextCollapser(tmp_path, threshold=10)
        large_content = "x" * 1000  # ~250 tokens
        assert collapser.should_collapse(large_content)

    def test_should_not_collapse_small_content(self, tmp_path: Path) -> None:
        collapser = ContextCollapser(tmp_path, threshold=1000)
        assert not collapser.should_collapse("small result")

    def test_collapse_persists_original(self, tmp_path: Path) -> None:
        collapser = ContextCollapser(tmp_path, threshold=10)
        content = "original full content " * 100
        entry = collapser.collapse("read_file", content, "Summary of file contents")
        assert entry.entry_id
        assert entry.tool_name == "read_file"
        assert "Summary" in entry.summary

        # Original should be persisted to disk
        original = Path(entry.original_path)
        assert original.exists()
        assert original.read_text() == content

    def test_expand_retrieves_original(self, tmp_path: Path) -> None:
        collapser = ContextCollapser(tmp_path, threshold=10)
        content = "the full original content"
        entry = collapser.collapse("execute", content, "Command output summary")
        expanded = collapser.expand(entry.entry_id)
        assert expanded == content

    def test_expand_nonexistent_returns_none(self, tmp_path: Path) -> None:
        collapser = ContextCollapser(tmp_path)
        assert collapser.expand("nonexistent") is None

    def test_format_collapsed(self, tmp_path: Path) -> None:
        collapser = ContextCollapser(tmp_path, threshold=10)
        entry = collapser.collapse("grep", "lots of results " * 100, "Found 42 matches")
        formatted = collapser.format_collapsed(entry)
        assert "Collapsed" in formatted
        assert "grep" in formatted
        assert "42 matches" in formatted
        assert "/expand" in formatted

    def test_list_entries(self, tmp_path: Path) -> None:
        collapser = ContextCollapser(tmp_path, threshold=10)
        collapser.collapse("tool1", "content1" * 100, "summary1")
        collapser.collapse("tool2", "content2" * 100, "summary2")
        entries = collapser.list_entries()
        assert len(entries) == 2


class TestSegmentProtocol:
    def _make_conversation(self) -> list:
        return [
            HumanMessage(content="Please fix the login bug in auth.py"),
            AIMessage(content="I'll look at the auth module."),
            ToolMessage(content="File: src/auth.py\ndef login(user):\n  ...", tool_call_id="tc1"),
            AIMessage(content="I found the issue. The session token isn't being set."),
            HumanMessage(content="No, that's not it. Check the password validation."),
            AIMessage(content="You're right, let me check the validation logic."),
            ToolMessage(content="Error: ValidationError: invalid hash format", tool_call_id="tc2"),
            AIMessage(content="Found it — the hash comparison is wrong."),
            HumanMessage(content="Yes, fix that please."),
            AIMessage(content="Fixed the hash comparison in auth.py."),
            ToolMessage(content="File written: src/auth.py", tool_call_id="tc3"),
        ]

    def test_parse_extracts_user_messages(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        assert len(segments.user_messages) == 3
        assert all(isinstance(m, HumanMessage) for m in segments.user_messages)

    def test_parse_preserves_user_messages_verbatim(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        assert segments.user_messages[0].content == "Please fix the login bug in auth.py"
        assert "password validation" in segments.user_messages[1].content

    def test_parse_extracts_original_request(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        assert "login bug" in segments.original_request

    def test_parse_extracts_files(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        assert any("auth.py" in f for f in segments.files_touched)

    def test_parse_extracts_errors(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        assert len(segments.errors_and_fixes) > 0
        assert any("ValidationError" in e for e in segments.errors_and_fixes)

    def test_build_summary_prompt(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        prompt = protocol.build_summary_prompt(segments)
        assert "Original Request" in prompt
        assert "Files Touched" in prompt
        assert "Errors" in prompt

    def test_reconstruct_preserves_user_messages(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        reconstructed = protocol.reconstruct("Summary of work done.", segments)

        # First message should be the compaction boundary
        assert isinstance(reconstructed[0], SystemMessage)
        assert "compacted" in reconstructed[0].content.lower()

        # All user messages should be preserved
        user_msgs = [m for m in reconstructed if isinstance(m, HumanMessage)]
        assert len(user_msgs) == 3
        assert user_msgs[1].content == "No, that's not it. Check the password validation."

    def test_reconstruct_includes_recent_messages(self) -> None:
        protocol = SegmentProtocol()
        messages = self._make_conversation()
        segments = protocol.parse(messages)
        recent = [AIMessage(content="Working on final fix.")]
        reconstructed = protocol.reconstruct("Summary.", segments, recent_messages=recent)
        assert any(
            isinstance(m, AIMessage) and "final fix" in m.content
            for m in reconstructed
        )

    def test_empty_conversation(self) -> None:
        protocol = SegmentProtocol()
        segments = protocol.parse([])
        assert len(segments.user_messages) == 0
        assert segments.original_request == ""
