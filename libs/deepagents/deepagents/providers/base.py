"""Base types for provider configuration and auxiliary model routing."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel


class ProviderType(enum.Enum):
    """Supported LLM provider backends."""

    OPENROUTER = "openrouter"
    OLLAMA = "ollama"


class AuxiliaryTask(enum.Enum):
    """Internal tasks that can be routed to cheaper models.

    These tasks do not require frontier-level intelligence and benefit
    from cost savings via smaller or local models.
    """

    COMPACTION = "compaction"
    CLASSIFICATION = "classification"
    MEMORY = "memory"


@dataclass(frozen=True)
class AuxiliaryModelConfig:
    """Configuration for routing auxiliary tasks to a cheap model.

    Auxiliary tasks (compaction summarization, permission classification,
    memory extraction) do not need frontier models. This config specifies
    a separate model to handle them, reducing cost.

    Attributes:
        model: Model identifier string (e.g., `qwen2.5:7b` for Ollama,
            `mistralai/mistral-small` for OpenRouter).
        provider: Which provider backend serves the auxiliary model.
        tasks: Which auxiliary tasks to route to this model.
            Defaults to all tasks.
        model_kwargs: Extra keyword arguments forwarded to the
            `ChatOpenAI` constructor (temperature, max_tokens, etc.).
    """

    model: str
    provider: ProviderType = ProviderType.OLLAMA
    tasks: frozenset[AuxiliaryTask] = field(
        default_factory=lambda: frozenset(AuxiliaryTask),
    )
    model_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderConfig:
    """Top-level provider configuration for a deep agent.

    Bundles the primary model specification with optional auxiliary
    model routing. Designed to slot into `create_deep_agent` without
    breaking the existing interface.

    Attributes:
        model: Primary model string in `provider/model` format
            (e.g., `anthropic/claude-sonnet-4-6`) or a pre-built
            `BaseChatModel` instance.
        auxiliary: Optional configuration for routing cheap internal
            tasks to a smaller model.
    """

    model: str | BaseChatModel
    auxiliary: AuxiliaryModelConfig | None = None
