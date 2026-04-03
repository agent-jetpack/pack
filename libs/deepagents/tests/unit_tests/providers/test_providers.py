"""Unit tests for the providers module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel

from deepagents.providers.base import (
    AuxiliaryModelConfig,
    AuxiliaryTask,
    ProviderConfig,
    ProviderType,
)
from deepagents.providers.ollama import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    OllamaProvider,
)
from deepagents.providers.openrouter import (
    OPENROUTER_API_KEY_ENV,
    OPENROUTER_BASE_URL,
    OpenRouterProvider,
    parse_model_string,
)


# -- base.py tests --


class TestProviderType:
    def test_values(self) -> None:
        assert ProviderType.OPENROUTER.value == "openrouter"
        assert ProviderType.OLLAMA.value == "ollama"


class TestAuxiliaryTask:
    def test_values(self) -> None:
        assert AuxiliaryTask.COMPACTION.value == "compaction"
        assert AuxiliaryTask.CLASSIFICATION.value == "classification"
        assert AuxiliaryTask.MEMORY.value == "memory"


class TestAuxiliaryModelConfig:
    def test_defaults(self) -> None:
        config = AuxiliaryModelConfig(model="qwen2.5:7b")
        assert config.model == "qwen2.5:7b"
        assert config.provider == ProviderType.OLLAMA
        assert config.tasks == frozenset(AuxiliaryTask)
        assert config.model_kwargs == {}

    def test_custom_tasks(self) -> None:
        tasks = frozenset({AuxiliaryTask.COMPACTION})
        config = AuxiliaryModelConfig(
            model="mistralai/mistral-small",
            provider=ProviderType.OPENROUTER,
            tasks=tasks,
        )
        assert config.provider == ProviderType.OPENROUTER
        assert AuxiliaryTask.COMPACTION in config.tasks
        assert AuxiliaryTask.MEMORY not in config.tasks

    def test_frozen(self) -> None:
        config = AuxiliaryModelConfig(model="test")
        with pytest.raises(AttributeError):
            config.model = "other"  # type: ignore[misc]


class TestProviderConfig:
    def test_string_model(self) -> None:
        config = ProviderConfig(model="anthropic/claude-sonnet-4-6")
        assert config.model == "anthropic/claude-sonnet-4-6"
        assert config.auxiliary is None

    def test_with_auxiliary(self) -> None:
        aux = AuxiliaryModelConfig(model="qwen2.5:7b")
        config = ProviderConfig(
            model="deepseek/deepseek-chat",
            auxiliary=aux,
        )
        assert config.auxiliary is aux

    def test_accepts_base_chat_model(self) -> None:
        mock_model = MagicMock(spec=BaseChatModel)
        config = ProviderConfig(model=mock_model)
        assert config.model is mock_model


# -- openrouter.py tests --


class TestParseModelString:
    def test_provider_slash_model(self) -> None:
        prefix, full = parse_model_string("anthropic/claude-sonnet-4-6")
        assert prefix == "anthropic"
        assert full == "anthropic/claude-sonnet-4-6"

    def test_nested_provider_model(self) -> None:
        prefix, full = parse_model_string("deepseek/deepseek-chat")
        assert prefix == "deepseek"
        assert full == "deepseek/deepseek-chat"

    def test_bare_model_name(self) -> None:
        prefix, full = parse_model_string("mistral-small")
        assert prefix is None
        assert full == "mistral-small"


class TestOpenRouterProvider:
    def test_init_with_explicit_key(self) -> None:
        provider = OpenRouterProvider(api_key="test-key")
        assert provider.api_key == "test-key"

    def test_init_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(OPENROUTER_API_KEY_ENV, "env-key")
        provider = OpenRouterProvider()
        assert provider.api_key == "env-key"

    def test_default_model(self) -> None:
        provider = OpenRouterProvider(api_key="k")
        assert provider.default_model == "anthropic/claude-sonnet-4-6"

    def test_custom_default_model(self) -> None:
        provider = OpenRouterProvider(
            api_key="k",
            default_model="deepseek/deepseek-chat",
        )
        assert provider.default_model == "deepseek/deepseek-chat"

    def test_create_model_no_key_raises(self) -> None:
        provider = OpenRouterProvider(api_key=None)
        # Ensure env is also clear
        with patch.dict("os.environ", {}, clear=True):
            provider_no_key = OpenRouterProvider()
        with pytest.raises(ValueError, match="API key not found"):
            provider_no_key.create_model()

    @patch("langchain_openai.ChatOpenAI")
    def test_create_model_passes_correct_args(
        self,
        mock_chat: MagicMock,
    ) -> None:
        provider = OpenRouterProvider(api_key="test-key")
        provider.create_model(model="deepseek/deepseek-chat", temperature=0.5)

        mock_chat.assert_called_once_with(
            model="deepseek/deepseek-chat",
            openai_api_key="test-key",
            openai_api_base=OPENROUTER_BASE_URL,
            temperature=0.5,
        )

    @patch("langchain_openai.ChatOpenAI")
    def test_create_model_uses_default_model(
        self,
        mock_chat: MagicMock,
    ) -> None:
        provider = OpenRouterProvider(api_key="k")
        provider.create_model()

        mock_chat.assert_called_once()
        call_kwargs = mock_chat.call_args
        assert call_kwargs.kwargs["model"] == "anthropic/claude-sonnet-4-6"

    @patch("langchain_openai.ChatOpenAI")
    def test_create_model_merges_kwargs(
        self,
        mock_chat: MagicMock,
    ) -> None:
        provider = OpenRouterProvider(
            api_key="k",
            model_kwargs={"temperature": 0.3, "max_tokens": 100},
        )
        provider.create_model(temperature=0.7)

        call_kwargs = mock_chat.call_args.kwargs
        # Per-call kwargs override constructor kwargs
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 100

    @patch("langchain_openai.ChatOpenAI")
    def test_create_auxiliary_model_fallback(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Without auxiliary config, falls back to a cheap OpenRouter model."""
        provider = OpenRouterProvider(api_key="k")
        provider.create_auxiliary_model(AuxiliaryTask.COMPACTION)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["model"] == "mistralai/mistral-small"

    @patch("langchain_openai.ChatOpenAI")
    def test_create_auxiliary_model_openrouter_config(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """With OpenRouter auxiliary config, uses specified model."""
        aux = AuxiliaryModelConfig(
            model="meta-llama/llama-3-8b",
            provider=ProviderType.OPENROUTER,
        )
        provider = OpenRouterProvider(api_key="k", auxiliary_config=aux)
        provider.create_auxiliary_model(AuxiliaryTask.MEMORY)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["model"] == "meta-llama/llama-3-8b"

    @patch("langchain_openai.ChatOpenAI")
    def test_create_auxiliary_model_task_not_in_config(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Task not covered by auxiliary config falls back to default."""
        aux = AuxiliaryModelConfig(
            model="test-model",
            provider=ProviderType.OPENROUTER,
            tasks=frozenset({AuxiliaryTask.COMPACTION}),
        )
        provider = OpenRouterProvider(api_key="k", auxiliary_config=aux)
        provider.create_auxiliary_model(AuxiliaryTask.MEMORY)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["model"] == "mistralai/mistral-small"

    @patch("deepagents.providers.ollama.OllamaProvider.is_available", return_value=True)
    @patch("langchain_openai.ChatOpenAI")
    def test_create_auxiliary_model_ollama_available(
        self,
        mock_ollama_chat: MagicMock,
        _mock_avail: MagicMock,
    ) -> None:
        """With Ollama auxiliary config and Ollama available, uses Ollama."""
        aux = AuxiliaryModelConfig(
            model="qwen2.5:7b",
            provider=ProviderType.OLLAMA,
        )
        provider = OpenRouterProvider(api_key="k", auxiliary_config=aux)
        provider.create_auxiliary_model(AuxiliaryTask.COMPACTION)

        mock_ollama_chat.assert_called_once()
        call_kwargs = mock_ollama_chat.call_args.kwargs
        assert call_kwargs["model"] == "qwen2.5:7b"

    @patch("deepagents.providers.ollama.OllamaProvider.is_available", return_value=False)
    @patch("langchain_openai.ChatOpenAI")
    def test_create_auxiliary_model_ollama_unavailable_fallback(
        self,
        mock_chat: MagicMock,
        _mock_avail: MagicMock,
    ) -> None:
        """With Ollama auxiliary config but Ollama down, falls back to OpenRouter."""
        aux = AuxiliaryModelConfig(
            model="qwen2.5:7b",
            provider=ProviderType.OLLAMA,
        )
        provider = OpenRouterProvider(api_key="k", auxiliary_config=aux)
        provider.create_auxiliary_model(AuxiliaryTask.COMPACTION)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["model"] == "mistralai/mistral-small"


# -- ollama.py tests --


class TestOllamaProvider:
    def test_default_base_url(self) -> None:
        provider = OllamaProvider()
        assert provider.base_url == DEFAULT_OLLAMA_BASE_URL

    def test_custom_base_url(self) -> None:
        provider = OllamaProvider(base_url="http://myhost:11434/v1")
        assert provider.base_url == "http://myhost:11434/v1"

    def test_base_url_appends_v1(self) -> None:
        provider = OllamaProvider(base_url="http://myhost:11434")
        assert provider.base_url == "http://myhost:11434/v1"

    def test_base_url_appends_v1_trailing_slash(self) -> None:
        provider = OllamaProvider(base_url="http://myhost:11434/")
        assert provider.base_url == "http://myhost:11434/v1"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://remote:11434")
        provider = OllamaProvider()
        assert provider.base_url == "http://remote:11434/v1"

    def test_default_model(self) -> None:
        provider = OllamaProvider()
        assert provider.default_model == DEFAULT_OLLAMA_MODEL

    @patch("langchain_openai.ChatOpenAI")
    def test_create_model(self, mock_chat: MagicMock) -> None:
        provider = OllamaProvider()
        provider.create_model(model="llama3:8b")

        mock_chat.assert_called_once_with(
            model="llama3:8b",
            openai_api_key="ollama",
            openai_api_base=DEFAULT_OLLAMA_BASE_URL,
        )

    @patch("langchain_openai.ChatOpenAI")
    def test_create_model_default(self, mock_chat: MagicMock) -> None:
        provider = OllamaProvider()
        provider.create_model()

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["model"] == DEFAULT_OLLAMA_MODEL

    @patch("langchain_openai.ChatOpenAI")
    def test_create_model_merges_kwargs(self, mock_chat: MagicMock) -> None:
        provider = OllamaProvider(model_kwargs={"temperature": 0.1})
        provider.create_model(max_tokens=200)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["max_tokens"] == 200

    @patch("deepagents.providers.ollama.urllib.request.urlopen")
    def test_is_available_true(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value.__enter__ = MagicMock()
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        provider = OllamaProvider()
        assert provider.is_available() is True

    @patch(
        "deepagents.providers.ollama.urllib.request.urlopen",
        side_effect=OSError("Connection refused"),
    )
    def test_is_available_false(self, _mock: MagicMock) -> None:
        provider = OllamaProvider()
        assert provider.is_available() is False


# -- __init__.py re-export tests --


class TestPackageReExports:
    def test_all_types_importable(self) -> None:
        from deepagents.providers import (
            AuxiliaryModelConfig,
            AuxiliaryTask,
            OllamaProvider,
            OpenRouterProvider,
            ProviderConfig,
            ProviderType,
        )

        # Verify they are the correct classes, not just any truthy value
        assert AuxiliaryModelConfig.__name__ == "AuxiliaryModelConfig"
        assert AuxiliaryTask.__name__ == "AuxiliaryTask"
        assert OllamaProvider.__name__ == "OllamaProvider"
        assert OpenRouterProvider.__name__ == "OpenRouterProvider"
        assert ProviderConfig.__name__ == "ProviderConfig"
        assert ProviderType.__name__ == "ProviderType"
