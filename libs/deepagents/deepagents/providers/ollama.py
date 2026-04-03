"""Ollama provider for local auxiliary models.

Uses `langchain-openai`'s `ChatOpenAI` pointed at a local Ollama server,
which exposes an OpenAI-compatible API.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any

from langchain_core.language_models import BaseChatModel

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
"""Default base URL for Ollama's OpenAI-compatible API endpoint."""

OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"
"""Environment variable to override the Ollama base URL."""

DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
"""Default local model for auxiliary tasks."""

_HEALTH_TIMEOUT_SECONDS = 2
"""Timeout for the Ollama health check request."""


class OllamaProvider:
    """Provider for local Ollama models.

    Wraps `langchain-openai`'s `ChatOpenAI` with Ollama's local
    OpenAI-compatible endpoint. Intended primarily for auxiliary tasks
    (compaction, classification, memory) to avoid API costs.

    Args:
        base_url: Ollama API base URL. Falls back to the
            `OLLAMA_BASE_URL` environment variable, then to the
            default localhost URL.
        default_model: Default model name for Ollama.
        model_kwargs: Extra kwargs forwarded to `ChatOpenAI`.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        default_model: str = DEFAULT_OLLAMA_MODEL,
        model_kwargs: dict[str, Any] | None = None,
    ) -> None:
        raw_base = base_url or os.environ.get(OLLAMA_BASE_URL_ENV)
        if raw_base and not raw_base.endswith("/v1"):
            raw_base = raw_base.rstrip("/") + "/v1"
        self._base_url = raw_base or DEFAULT_OLLAMA_BASE_URL
        self._default_model = default_model
        self._model_kwargs: dict[str, Any] = model_kwargs or {}

    @property
    def base_url(self) -> str:
        """The resolved Ollama API base URL."""
        return self._base_url

    @property
    def default_model(self) -> str:
        """The default model identifier."""
        return self._default_model

    def is_available(self) -> bool:
        """Check whether the Ollama server is reachable.

        Sends a lightweight HTTP request to the Ollama root endpoint
        (without `/v1` suffix) to verify the server is running.

        Returns:
            `True` if the server responds, `False` otherwise.
        """
        # Strip /v1 to hit the root health endpoint
        health_url = self._base_url.removesuffix("/v1")
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT_SECONDS):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def create_model(
        self,
        model: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Create a `ChatOpenAI` instance pointed at the local Ollama server.

        Ollama does not require an API key, so a placeholder value is
        used to satisfy the `ChatOpenAI` constructor.

        Args:
            model: Model name (e.g., `qwen2.5:7b`). Uses
                `default_model` if not provided.
            **kwargs: Additional keyword arguments forwarded to
                `ChatOpenAI`.

        Returns:
            A configured `BaseChatModel` for the requested model.
        """
        from langchain_openai import ChatOpenAI

        resolved = model or self._default_model
        merged_kwargs = {**self._model_kwargs, **kwargs}

        return ChatOpenAI(
            model=resolved,
            openai_api_key="ollama",  # Ollama ignores the key
            openai_api_base=self._base_url,
            **merged_kwargs,
        )
