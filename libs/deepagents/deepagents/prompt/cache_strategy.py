"""Provider-aware cache strategies for system prompt sections.

Different LLM providers handle prompt caching differently:

- **Anthropic**: Requires explicit `cache_control` breakpoints to mark
  where cached content ends.
- **OpenAI**: Caches automatically for prompts longer than 1024 tokens.
- **Open-source models**: No prompt caching support.

The `detect_strategy` factory picks the right strategy based on model name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from deepagents.prompt.sections import PromptSection


@runtime_checkable
class CacheStrategy(Protocol):
    """Protocol for provider-specific prompt cache annotation.

    Implementations decide how (or whether) to inject cache control
    markers into the assembled prompt sections.
    """

    def annotate(
        self,
        sections: list[PromptSection],
    ) -> list[dict[str, Any]]:
        """Convert prompt sections into provider-formatted content blocks.

        Args:
            sections: Ordered list of prompt sections with cacheability flags.

        Returns:
            A list of content block dicts ready for the provider's API.
            The exact shape depends on the provider.
        """
        ...


class AnthropicCacheStrategy:
    """Injects a `cache_control` breakpoint at the static/dynamic boundary.

    Anthropic's prompt caching requires an explicit marker on the last
    cacheable content block. All content before that marker is cached;
    content after it is re-processed each request.
    """

    def annotate(
        self,
        sections: list[PromptSection],
    ) -> list[dict[str, Any]]:
        """Annotate sections with Anthropic cache control markers.

        Finds the last cacheable section and attaches a
        `cache_control: {"type": "ephemeral"}` marker to it.

        Args:
            sections: Ordered list of prompt sections.

        Returns:
            Content blocks with cache control on the boundary section.
        """
        last_cacheable_idx = -1
        for i, section in enumerate(sections):
            if section.cacheable:
                last_cacheable_idx = i

        blocks: list[dict[str, Any]] = []
        for i, section in enumerate(sections):
            block: dict[str, Any] = {
                "type": "text",
                "text": section.content,
            }
            if i == last_cacheable_idx:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)

        return blocks


class OpenAICacheStrategy:
    """No-op strategy for OpenAI-compatible providers.

    OpenAI automatically caches prompts longer than 1024 tokens.
    No explicit markers are needed.
    """

    def annotate(
        self,
        sections: list[PromptSection],
    ) -> list[dict[str, Any]]:
        """Return plain content blocks without cache markers.

        Args:
            sections: Ordered list of prompt sections.

        Returns:
            Plain text content blocks.
        """
        return [
            {"type": "text", "text": section.content}
            for section in sections
        ]


class DefaultCacheStrategy:
    """No-op fallback for open-source and unsupported models.

    Models served via Ollama or other local runtimes typically
    have no prompt caching mechanism.
    """

    def annotate(
        self,
        sections: list[PromptSection],
    ) -> list[dict[str, Any]]:
        """Return plain content blocks without cache markers.

        Args:
            sections: Ordered list of prompt sections.

        Returns:
            Plain text content blocks.
        """
        return [
            {"type": "text", "text": section.content}
            for section in sections
        ]


# Prefixes that identify Anthropic models on OpenRouter
_ANTHROPIC_PREFIXES = ("anthropic/", "claude")
# Prefixes that identify OpenAI models on OpenRouter
_OPENAI_PREFIXES = ("openai/", "gpt-", "o1-", "o3-", "o4-")


def detect_strategy(model_name: str) -> CacheStrategy:
    """Select the appropriate cache strategy for a model.

    Uses model name prefixes to determine the provider and returns
    the matching strategy. Falls back to `DefaultCacheStrategy` for
    unrecognized models.

    Args:
        model_name: Model identifier, optionally with provider prefix
            (e.g., `anthropic/claude-sonnet-4-6`, `gpt-4o`,
            `deepseek/deepseek-chat`).

    Returns:
        A `CacheStrategy` instance for the detected provider.
    """
    lower = model_name.lower()

    if any(lower.startswith(p) for p in _ANTHROPIC_PREFIXES):
        return AnthropicCacheStrategy()

    if any(lower.startswith(p) for p in _OPENAI_PREFIXES):
        return OpenAICacheStrategy()

    return DefaultCacheStrategy()
