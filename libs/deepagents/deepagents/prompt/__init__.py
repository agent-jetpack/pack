"""System prompt builder with provider-aware cache boundaries.

This module assembles the system prompt from modular sections and
applies cache control annotations appropriate for the target LLM
provider.
"""

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

__all__ = [
    "AnthropicCacheStrategy",
    "CacheStrategy",
    "DefaultCacheStrategy",
    "OpenAICacheStrategy",
    "PromptSection",
    "SystemPromptBuilder",
    "detect_strategy",
    "environment_section",
    "git_section",
    "identity_section",
    "safety_section",
    "style_section",
    "tool_rules_section",
]
