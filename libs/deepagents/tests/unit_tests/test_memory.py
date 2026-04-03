"""Unit tests for the structured memory module."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from deepagents.memory.dream import DreamConsolidator
from deepagents.memory.extractor import MemoryExtractor
from deepagents.memory.index import MemoryIndex, _extract_name_from_index_line
from deepagents.memory.taxonomy import (
    MemoryCategory,
    MemoryEntry,
    validate_not_code_fact,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Taxonomy tests
# ---------------------------------------------------------------------------


class TestMemoryCategory:
    """Tests for MemoryCategory enum."""

    def test_all_categories_have_save_triggers(self) -> None:
        for cat in MemoryCategory:
            assert cat.save_trigger, f"{cat} has no save trigger"

    def test_all_categories_have_use_rules(self) -> None:
        for cat in MemoryCategory:
            assert cat.use_rule, f"{cat} has no use rule"

    def test_category_values(self) -> None:
        assert MemoryCategory.USER.value == "user"
        assert MemoryCategory.FEEDBACK.value == "feedback"
        assert MemoryCategory.PROJECT.value == "project"
        assert MemoryCategory.REFERENCE.value == "reference"


class TestValidateNotCodeFact:
    """Tests for the code-fact validation function."""

    def test_rejects_function_definition(self) -> None:
        errors = validate_not_code_fact("def process_data(items):")
        assert len(errors) == 1
        assert "code fact" in errors[0].lower()

    def test_rejects_async_function_definition(self) -> None:
        errors = validate_not_code_fact("async def fetch_users():")
        assert len(errors) == 1

    def test_rejects_file_path(self) -> None:
        errors = validate_not_code_fact("The handler is at /src/api/handler.py")
        assert len(errors) == 1

    def test_rejects_import_statement(self) -> None:
        errors = validate_not_code_fact("from deepagents.memory import MemoryIndex")
        assert len(errors) == 1

    def test_rejects_class_method_reference(self) -> None:
        errors = validate_not_code_fact("Call MyClass.process() to run it")
        assert len(errors) == 1

    def test_allows_plain_preference(self) -> None:
        errors = validate_not_code_fact(
            "User prefers concise responses with examples"
        )
        assert errors == []

    def test_allows_code_reference_with_hint_phrase(self) -> None:
        errors = validate_not_code_fact(
            "User prefers the pattern in /src/utils.py for helper functions"
        )
        assert errors == []

    def test_allows_behavioral_correction(self) -> None:
        errors = validate_not_code_fact(
            "Always use type hints in function signatures"
        )
        assert errors == []

    def test_allows_empty_string(self) -> None:
        errors = validate_not_code_fact("")
        assert errors == []


class TestMemoryEntry:
    """Tests for MemoryEntry dataclass."""

    def _make_entry(self, **kwargs: object) -> MemoryEntry:
        defaults: dict[str, object] = {
            "name": "test_entry",
            "description": "A test entry for unit tests",
            "category": MemoryCategory.USER,
            "content": "User prefers dark mode in all editors",
        }
        defaults.update(kwargs)
        return MemoryEntry(**defaults)  # type: ignore[arg-type]

    def test_validate_valid_entry(self) -> None:
        entry = self._make_entry()
        assert entry.validate() == []

    def test_validate_empty_name(self) -> None:
        entry = self._make_entry(name="  ")
        errors = entry.validate()
        assert any("name" in e.lower() for e in errors)

    def test_validate_long_description(self) -> None:
        entry = self._make_entry(description="x" * 201)
        errors = entry.validate()
        assert any("200" in e for e in errors)

    def test_validate_empty_content(self) -> None:
        entry = self._make_entry(content="  ")
        errors = entry.validate()
        assert any("content" in e.lower() for e in errors)

    def test_validate_rejects_code_fact_content(self) -> None:
        entry = self._make_entry(content="def my_function(x, y): return x + y")
        errors = entry.validate()
        assert any("code fact" in e.lower() for e in errors)

    def test_hint_defaults_to_true(self) -> None:
        entry = self._make_entry()
        assert entry.hint is True

    def test_to_index_line(self) -> None:
        entry = self._make_entry()
        line = entry.to_index_line()
        assert "[user]" in line
        assert "**test_entry**" in line
        assert "(hint)" in line

    def test_to_index_line_no_hint(self) -> None:
        entry = self._make_entry(hint=False)
        line = entry.to_index_line()
        assert "(hint)" not in line

    def test_roundtrip_markdown(self) -> None:
        entry = self._make_entry()
        md = entry.to_markdown()
        parsed = MemoryEntry.from_markdown(md)
        assert parsed.name == entry.name
        assert parsed.description == entry.description
        assert parsed.category == entry.category
        assert parsed.content == entry.content
        assert parsed.hint == entry.hint

    def test_from_markdown_missing_frontmatter(self) -> None:
        with pytest.raises(ValueError, match="YAML frontmatter"):
            MemoryEntry.from_markdown("No frontmatter here")

    def test_from_markdown_missing_required_fields(self) -> None:
        text = "---\nname: test\n---\nSome content"
        with pytest.raises(ValueError, match="Missing required"):
            MemoryEntry.from_markdown(text)


# ---------------------------------------------------------------------------
# Index tests
# ---------------------------------------------------------------------------


class TestExtractNameFromIndexLine:
    """Tests for the index line parser."""

    def test_standard_format(self) -> None:
        line = "- [user] **dark_mode**: Prefers dark mode (hint)"
        assert _extract_name_from_index_line(line) == "dark_mode"

    def test_no_bold_markers(self) -> None:
        line = "- [user] dark_mode: Prefers dark mode"
        assert _extract_name_from_index_line(line) is None

    def test_empty_line(self) -> None:
        assert _extract_name_from_index_line("") is None

    def test_header_line(self) -> None:
        assert _extract_name_from_index_line("## User") is None


class TestMemoryIndex:
    """Tests for MemoryIndex file operations."""

    def test_init_default_dir(self) -> None:
        idx = MemoryIndex()
        assert "memories" in str(idx.root)

    def test_init_custom_dir(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        assert idx.root == tmp_path / "mem"

    def test_ensure_dirs(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        idx.ensure_dirs()
        assert (tmp_path / "mem" / "user").is_dir()
        assert (tmp_path / "mem" / "feedback").is_dir()
        assert (tmp_path / "mem" / "project").is_dir()
        assert (tmp_path / "mem" / "reference").is_dir()

    def test_load_index_empty(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        lines = idx.load_index()
        assert lines == []

    def test_add_and_search(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        entry = MemoryEntry(
            name="dark_mode",
            description="User prefers dark mode in editors",
            category=MemoryCategory.USER,
            content="User always wants dark theme enabled",
        )
        errors = idx.add(entry)
        assert errors == []

        results = idx.search("dark mode")
        assert len(results) == 1
        assert results[0].name == "dark_mode"

    def test_add_duplicate_rejected(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        entry = MemoryEntry(
            name="style",
            description="Coding style preferences",
            category=MemoryCategory.PROJECT,
            content="User prefers functional style over OOP",
        )
        idx.add(entry)
        errors = idx.add(entry)
        assert any("already exists" in e for e in errors)

    def test_add_validates_entry(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        entry = MemoryEntry(
            name="",
            description="Bad entry",
            category=MemoryCategory.USER,
            content="Some content",
        )
        errors = idx.add(entry)
        assert len(errors) > 0

    def test_remove(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        entry = MemoryEntry(
            name="removeme",
            description="Entry to be removed",
            category=MemoryCategory.FEEDBACK,
            content="User prefers shorter responses",
        )
        idx.add(entry)
        assert idx.remove("removeme") is True
        assert idx.search("removeme") == []

    def test_remove_nonexistent(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        assert idx.remove("nonexistent") is False

    def test_list_entries_all(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        for i, cat in enumerate(MemoryCategory):
            entry = MemoryEntry(
                name=f"entry_{cat.value}",
                description=f"Test entry {i}",
                category=cat,
                content=f"User prefers approach {i}",
            )
            idx.add(entry)

        entries = idx.list_entries()
        assert len(entries) == 4  # one per category

    def test_list_entries_filtered(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        idx.add(
            MemoryEntry(
                name="user_pref",
                description="A user preference",
                category=MemoryCategory.USER,
                content="User prefers vim keybindings",
            )
        )
        idx.add(
            MemoryEntry(
                name="proj_conv",
                description="A project convention",
                category=MemoryCategory.PROJECT,
                content="Team prefers trunk-based development",
            )
        )

        user_entries = idx.list_entries(category=MemoryCategory.USER)
        assert len(user_entries) == 1
        assert user_entries[0].name == "user_pref"

    def test_index_file_written(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        idx.add(
            MemoryEntry(
                name="written",
                description="Check file is written",
                category=MemoryCategory.USER,
                content="User prefers tabs over spaces",
            )
        )
        assert idx.index_path.exists()
        content = idx.index_path.read_text(encoding="utf-8")
        assert "**written**" in content
        assert "[user]" in content

    def test_search_no_match(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        idx.add(
            MemoryEntry(
                name="alpha",
                description="Alpha preference",
                category=MemoryCategory.USER,
                content="User prefers alpha approach",
            )
        )
        results = idx.search("zzznonexistent")
        assert results == []


# ---------------------------------------------------------------------------
# Extractor tests
# ---------------------------------------------------------------------------


def _make_mock_model(response: str) -> object:
    """Create a mock model callable that returns a fixed response.

    Args:
        response: The string to return for any prompt.

    Returns:
        A callable matching the ExtractionModel protocol.
    """

    def mock(_prompt: str) -> str:
        return response

    return mock


class TestMemoryExtractor:
    """Tests for MemoryExtractor rate limiting and extraction."""

    def test_rate_limiting_initial_state(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        model = _make_mock_model("[]")
        extractor = MemoryExtractor(idx, model, interval=3)

        # Initial state: turn_count=0, last=-3, diff=3 >= 3 -> ready to extract
        assert extractor.should_extract() is True

    def test_first_turn_extracts(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        model = _make_mock_model("[]")
        extractor = MemoryExtractor(idx, model, interval=3)

        result = extractor.on_turn("Hello")
        assert result == []
        assert extractor.turn_count == 1

    def test_skips_intermediate_turns(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        call_count = 0

        def counting_model(_prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "[]"

        extractor = MemoryExtractor(idx, counting_model, interval=3)

        # Turn 1: extracts (first turn)
        extractor.on_turn("Turn 1")
        assert call_count == 1

        # Turns 2-3: skipped
        extractor.on_turn("Turn 2")
        extractor.on_turn("Turn 3")
        assert call_count == 1

        # Turn 4: extracts again
        extractor.on_turn("Turn 4")
        assert call_count == 2

    def test_extracts_valid_entry(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        response = json.dumps([
            {
                "name": "concise_responses",
                "description": "User wants shorter replies",
                "category": "feedback",
                "content": "User prefers concise responses without filler",
            }
        ])
        model = _make_mock_model(response)
        extractor = MemoryExtractor(idx, model, interval=1)

        result = extractor.on_turn("Keep it short please")
        assert len(result) == 1
        assert result[0].name == "concise_responses"
        assert result[0].category == MemoryCategory.FEEDBACK

    def test_rejects_code_fact_extraction(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        response = json.dumps([
            {
                "name": "handler_location",
                "description": "Handler function location",
                "category": "project",
                "content": "def handle_request(req): processes incoming requests",
            }
        ])
        model = _make_mock_model(response)
        extractor = MemoryExtractor(idx, model, interval=1)

        result = extractor.on_turn("Where is the handler?")
        assert result == []

    def test_handles_invalid_json(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        model = _make_mock_model("not json at all")
        extractor = MemoryExtractor(idx, model, interval=1)

        result = extractor.on_turn("Something")
        assert result == []

    def test_handles_model_exception(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))

        def failing_model(_prompt: str) -> str:
            msg = "API error"
            raise RuntimeError(msg)

        extractor = MemoryExtractor(idx, failing_model, interval=1)

        result = extractor.on_turn("Something")
        assert result == []

    def test_strips_markdown_fences(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        response = (
            "```json\n"
            + json.dumps([
                {
                    "name": "fenced",
                    "description": "Entry from fenced response",
                    "category": "user",
                    "content": "User prefers markdown formatting",
                }
            ])
            + "\n```"
        )
        model = _make_mock_model(response)
        extractor = MemoryExtractor(idx, model, interval=1)

        result = extractor.on_turn("Test")
        assert len(result) == 1
        assert result[0].name == "fenced"


# ---------------------------------------------------------------------------
# Dream consolidator tests
# ---------------------------------------------------------------------------


class TestDreamConsolidator:
    """Tests for DreamConsolidator transcript processing."""

    def test_find_no_transcripts(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        model = _make_mock_model("[]")
        dc = DreamConsolidator(
            idx, model, transcript_dir=str(tmp_path / "transcripts")
        )
        assert dc.find_recent_transcripts() == []

    def test_find_recent_transcripts(self, tmp_path: Path) -> None:
        t_dir = tmp_path / "transcripts"
        t_dir.mkdir()
        (t_dir / "session1.md").write_text("transcript 1")
        (t_dir / "session2.md").write_text("transcript 2")
        (t_dir / "notes.txt").write_text("not a transcript")

        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        model = _make_mock_model("[]")
        dc = DreamConsolidator(idx, model, transcript_dir=str(t_dir))

        paths = dc.find_recent_transcripts()
        assert len(paths) == 2  # only .md files
        assert all(p.suffix == ".md" for p in paths)

    def test_read_transcripts(self, tmp_path: Path) -> None:
        t_dir = tmp_path / "transcripts"
        t_dir.mkdir()
        (t_dir / "s1.md").write_text("hello world")

        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        model = _make_mock_model("[]")
        dc = DreamConsolidator(idx, model, transcript_dir=str(t_dir))

        text = dc.read_transcripts([t_dir / "s1.md"])
        assert "hello world" in text
        assert "s1.md" in text

    def test_consolidate_empty(self, tmp_path: Path) -> None:
        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        model = _make_mock_model("[]")
        dc = DreamConsolidator(
            idx, model, transcript_dir=str(tmp_path / "transcripts")
        )

        result = dc.consolidate()
        assert result == []

    def test_consolidate_extracts_patterns(self, tmp_path: Path) -> None:
        t_dir = tmp_path / "transcripts"
        t_dir.mkdir()
        (t_dir / "session.md").write_text(
            "User: Use shorter variable names\n"
            "Agent: OK\n"
            "User: I said shorter names!\n"
        )

        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        response = json.dumps([
            {
                "name": "short_names",
                "description": "User repeatedly asks for shorter variable names",
                "category": "feedback",
                "content": "User prefers short, concise variable names",
            }
        ])
        model = _make_mock_model(response)
        dc = DreamConsolidator(idx, model, transcript_dir=str(t_dir))

        result = dc.consolidate()
        assert len(result) == 1
        assert result[0].name == "short_names"

    def test_consolidate_rejects_code_facts(self, tmp_path: Path) -> None:
        t_dir = tmp_path / "transcripts"
        t_dir.mkdir()
        (t_dir / "session.md").write_text("Some session content")

        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))
        response = json.dumps([
            {
                "name": "handler_sig",
                "description": "Handler function signature",
                "category": "project",
                "content": "def handle_request(req, res): pass",
            }
        ])
        model = _make_mock_model(response)
        dc = DreamConsolidator(idx, model, transcript_dir=str(t_dir))

        result = dc.consolidate()
        assert result == []

    def test_consolidate_handles_model_failure(self, tmp_path: Path) -> None:
        t_dir = tmp_path / "transcripts"
        t_dir.mkdir()
        (t_dir / "session.md").write_text("Content")

        idx = MemoryIndex(memory_dir=str(tmp_path / "mem"))

        def failing_model(_prompt: str) -> str:
            msg = "Model unavailable"
            raise RuntimeError(msg)

        dc = DreamConsolidator(idx, failing_model, transcript_dir=str(t_dir))

        result = dc.consolidate()
        assert result == []
