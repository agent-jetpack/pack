"""Unit tests for the prompt builder module."""

from __future__ import annotations

from deepagents.prompt.builder import SystemPromptBuilder
from deepagents.prompt.cache_strategy import (
    AnthropicCacheStrategy,
    CacheStrategy,
    DefaultCacheStrategy,
    OpenAICacheStrategy,
    detect_strategy,
)
from deepagents.prompt.sections import (
    PromptSection,
    environment_section,
    git_section,
    identity_section,
    safety_section,
    style_section,
    tool_rules_section,
)

# -- PromptSection tests --


class TestPromptSection:
    def test_frozen(self) -> None:
        section = PromptSection(content="hello", cacheable=True)
        assert section.content == "hello"
        assert section.cacheable is True

    def test_equality(self) -> None:
        a = PromptSection(content="x", cacheable=True)
        b = PromptSection(content="x", cacheable=True)
        assert a == b


# -- Section builder tests --


class TestStaticSections:
    def test_identity_is_cacheable(self) -> None:
        section = identity_section()
        assert section.cacheable is True
        assert "Deep Agent" in section.content

    def test_safety_is_cacheable(self) -> None:
        section = safety_section()
        assert section.cacheable is True
        assert "Core Behavior" in section.content

    def test_tool_rules_is_cacheable(self) -> None:
        section = tool_rules_section()
        assert section.cacheable is True
        assert "read_file" in section.content

    def test_style_is_cacheable(self) -> None:
        section = style_section()
        assert section.cacheable is True
        assert "Doing Tasks" in section.content


class TestDynamicSections:
    def test_environment_is_not_cacheable(self) -> None:
        section = environment_section(cwd="/home/user", os_info="Linux 6.1")
        assert section.cacheable is False
        assert "/home/user" in section.content
        assert "Linux 6.1" in section.content

    def test_git_is_not_cacheable(self) -> None:
        section = git_section(branch="main", status="clean")
        assert section.cacheable is False
        assert "main" in section.content
        assert "clean" in section.content


# -- CacheStrategy tests --


class TestDetectStrategy:
    def test_anthropic_prefix(self) -> None:
        strategy = detect_strategy("anthropic/claude-sonnet-4-6")
        assert isinstance(strategy, AnthropicCacheStrategy)

    def test_claude_bare_name(self) -> None:
        strategy = detect_strategy("claude-sonnet-4-6")
        assert isinstance(strategy, AnthropicCacheStrategy)

    def test_openai_prefix(self) -> None:
        strategy = detect_strategy("openai/gpt-4o")
        assert isinstance(strategy, OpenAICacheStrategy)

    def test_gpt_bare_name(self) -> None:
        strategy = detect_strategy("gpt-4o")
        assert isinstance(strategy, OpenAICacheStrategy)

    def test_o1_prefix(self) -> None:
        strategy = detect_strategy("o1-mini")
        assert isinstance(strategy, OpenAICacheStrategy)

    def test_deepseek_gets_default(self) -> None:
        strategy = detect_strategy("deepseek/deepseek-chat")
        assert isinstance(strategy, DefaultCacheStrategy)

    def test_unknown_model_gets_default(self) -> None:
        strategy = detect_strategy("some-random-model")
        assert isinstance(strategy, DefaultCacheStrategy)

    def test_case_insensitive(self) -> None:
        strategy = detect_strategy("Anthropic/Claude-Sonnet-4-6")
        assert isinstance(strategy, AnthropicCacheStrategy)


class TestAnthropicCacheStrategy:
    def test_cache_control_on_last_cacheable(self) -> None:
        sections = [
            PromptSection(content="static1", cacheable=True),
            PromptSection(content="static2", cacheable=True),
            PromptSection(content="dynamic1", cacheable=False),
        ]
        strategy = AnthropicCacheStrategy()
        blocks = strategy.annotate(sections)

        assert len(blocks) == 3
        # First static block: no cache_control
        assert "cache_control" not in blocks[0]
        # Last static block: has cache_control
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}
        # Dynamic block: no cache_control
        assert "cache_control" not in blocks[2]

    def test_all_cacheable(self) -> None:
        sections = [
            PromptSection(content="a", cacheable=True),
            PromptSection(content="b", cacheable=True),
        ]
        blocks = AnthropicCacheStrategy().annotate(sections)
        assert "cache_control" not in blocks[0]
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}

    def test_no_cacheable_sections(self) -> None:
        sections = [PromptSection(content="d", cacheable=False)]
        blocks = AnthropicCacheStrategy().annotate(sections)
        assert "cache_control" not in blocks[0]

    def test_empty_sections(self) -> None:
        blocks = AnthropicCacheStrategy().annotate([])
        assert blocks == []


class TestOpenAICacheStrategy:
    def test_no_cache_markers(self) -> None:
        sections = [
            PromptSection(content="a", cacheable=True),
            PromptSection(content="b", cacheable=False),
        ]
        blocks = OpenAICacheStrategy().annotate(sections)
        for block in blocks:
            assert "cache_control" not in block
        assert blocks[0]["text"] == "a"
        assert blocks[1]["text"] == "b"


class TestDefaultCacheStrategy:
    def test_no_cache_markers(self) -> None:
        sections = [PromptSection(content="x", cacheable=True)]
        blocks = DefaultCacheStrategy().annotate(sections)
        assert blocks == [{"type": "text", "text": "x"}]


class TestCacheStrategyProtocol:
    def test_strategies_satisfy_protocol(self) -> None:
        assert isinstance(AnthropicCacheStrategy(), CacheStrategy)
        assert isinstance(OpenAICacheStrategy(), CacheStrategy)
        assert isinstance(DefaultCacheStrategy(), CacheStrategy)


# -- SystemPromptBuilder tests --


class TestSystemPromptBuilder:
    def test_build_returns_content_blocks(self) -> None:
        builder = SystemPromptBuilder(model_name="deepseek/deepseek-chat")
        blocks = builder.build()
        assert isinstance(blocks, list)
        assert len(blocks) >= 4  # at least identity, safety, tools, style
        for block in blocks:
            assert block["type"] == "text"
            assert "text" in block

    def test_build_with_dynamic_sections(self) -> None:
        builder = SystemPromptBuilder(model_name="deepseek/deepseek-chat")
        blocks = builder.build(
            cwd="/home/user",
            os_info="Linux 6.1",
            branch="main",
            git_status="clean",
        )
        texts = [b["text"] for b in blocks]
        combined = "\n".join(texts)
        assert "/home/user" in combined
        assert "main" in combined

    def test_build_text_returns_string(self) -> None:
        builder = SystemPromptBuilder(model_name="deepseek/deepseek-chat")
        text = builder.build_text()
        assert isinstance(text, str)
        assert "Deep Agent" in text

    def test_anthropic_model_gets_cache_control(self) -> None:
        builder = SystemPromptBuilder(
            model_name="anthropic/claude-sonnet-4-6",
        )
        blocks = builder.build()
        cache_blocks = [b for b in blocks if "cache_control" in b]
        assert len(cache_blocks) == 1
        assert cache_blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_explicit_strategy_overrides_detection(self) -> None:
        builder = SystemPromptBuilder(
            model_name="anthropic/claude-sonnet-4-6",
            strategy=DefaultCacheStrategy(),
        )
        blocks = builder.build()
        for block in blocks:
            assert "cache_control" not in block

    def test_add_static_section(self) -> None:
        builder = SystemPromptBuilder()
        builder.add_static_section("Custom guideline content.")
        blocks = builder.build()
        texts = [b["text"] for b in blocks]
        assert "Custom guideline content." in texts

    def test_add_dynamic_section(self) -> None:
        builder = SystemPromptBuilder()
        builder.add_dynamic_section("MCP tools: list_files, run_query")
        blocks = builder.build()
        texts = [b["text"] for b in blocks]
        assert "MCP tools: list_files, run_query" in texts

    def test_static_before_dynamic(self) -> None:
        builder = SystemPromptBuilder(
            model_name="anthropic/claude-sonnet-4-6",
        )
        builder.add_static_section("STATIC_CUSTOM")
        builder.add_dynamic_section("DYNAMIC_CUSTOM")
        blocks = builder.build(
            cwd="/workspace",
            os_info="Darwin 24.0",
            branch="feat/test",
            git_status="1 modified",
        )
        texts = [b["text"] for b in blocks]
        static_idx = texts.index("STATIC_CUSTOM")
        dynamic_idx = texts.index("DYNAMIC_CUSTOM")
        assert static_idx < dynamic_idx

    def test_strategy_property(self) -> None:
        builder = SystemPromptBuilder(model_name="gpt-4o")
        assert isinstance(builder.strategy, OpenAICacheStrategy)

    def test_default_strategy_when_no_model(self) -> None:
        builder = SystemPromptBuilder()
        assert isinstance(builder.strategy, DefaultCacheStrategy)

    def test_partial_dynamic_env_only(self) -> None:
        builder = SystemPromptBuilder()
        blocks = builder.build(cwd="/home", os_info="Linux")
        texts = [b["text"] for b in blocks]
        combined = "\n".join(texts)
        assert "/home" in combined
        # No git section when branch/status not provided
        assert "Git Context" not in combined

    def test_partial_dynamic_git_only(self) -> None:
        builder = SystemPromptBuilder()
        blocks = builder.build(branch="main", git_status="clean")
        texts = [b["text"] for b in blocks]
        combined = "\n".join(texts)
        assert "main" in combined
        # No environment section when cwd/os_info not provided
        assert "Environment" not in combined
