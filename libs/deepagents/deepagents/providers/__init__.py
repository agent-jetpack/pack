"""Provider abstractions for model routing.

This module provides a unified interface for configuring LLM providers
(OpenRouter, Ollama) and routing auxiliary tasks to cheaper models.
"""

from deepagents.providers.base import (
    AuxiliaryModelConfig,
    AuxiliaryTask,
    ProviderConfig,
    ProviderType,
)
from deepagents.providers.ollama import OllamaProvider
from deepagents.providers.openrouter import OpenRouterProvider

__all__ = [
    "AuxiliaryModelConfig",
    "AuxiliaryTask",
    "OllamaProvider",
    "OpenRouterProvider",
    "ProviderConfig",
    "ProviderType",
]
