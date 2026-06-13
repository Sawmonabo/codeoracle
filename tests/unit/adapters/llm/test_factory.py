"""Plan 4 Task 12: LLMProviderFactory unit tests."""

from codeoracle.adapters.llm.config import (
    AnthropicProviderConfig,
    LLMConfig,
    MockLLMProviderConfig,
    OllamaProviderConfig,
    OpenAIProviderConfig,
    VLLMProviderConfig,
)
from codeoracle.adapters.llm.factory import LLMProviderFactory
from codeoracle.adapters.llm.providers.anthropic import AnthropicProvider
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.adapters.llm.providers.ollama import OllamaProvider
from codeoracle.adapters.llm.providers.openai import OpenAIProvider
from codeoracle.adapters.llm.providers.vllm import VLLMProvider


def test_factory_creates_openai():
    cfg = LLMConfig(
        provider="openai",
        openai=OpenAIProviderConfig.model_validate({"api_key": "sk-x"}),
    )
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, OpenAIProvider)


def test_factory_creates_anthropic():
    cfg = LLMConfig(
        provider="anthropic",
        anthropic=AnthropicProviderConfig.model_validate(
            {"api_key": "sk-ant-x"}
        ),
    )
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, AnthropicProvider)


def test_factory_creates_vllm():
    cfg = LLMConfig(provider="vllm", vllm=VLLMProviderConfig())
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, VLLMProvider)


def test_factory_creates_ollama():
    cfg = LLMConfig(provider="ollama", ollama=OllamaProviderConfig())
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, OllamaProvider)


def test_factory_creates_mock(tmp_path):
    cfg = LLMConfig(
        provider="mock", mock=MockLLMProviderConfig(scripts_dir=str(tmp_path))
    )
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, MockLLMProvider)
