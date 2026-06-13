"""Plan 4 Task 6: LLMConfig unit tests."""

import pytest
from pydantic import SecretStr, ValidationError

from codeoracle.adapters.llm.config import (
    AnthropicProviderConfig,
    LLMConfig,
    MockLLMProviderConfig,
    OpenAIProviderConfig,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Isolate config tests from developer shell + repo .env file.

    `monkeypatch.chdir(tmp_path)` escapes the repo's `.env` (pydantic-settings
    reads it from the CWD). Explicit `delenv` for each CODEORACLE_LLM_* var
    prevents
    cross-test pollution from the developer's shell — `monkeypatch.delenv` is
    auto-rolled-back at test teardown, so the developer's env is untouched
    outside this fixture's scope.
    """
    monkeypatch.chdir(tmp_path)
    for var in (
        "CODEORACLE_LLM_PROVIDER",
        "CODEORACLE_LLM_OPENAI__API_KEY",
        "CODEORACLE_LLM_OPENAI__MODEL",
        "CODEORACLE_LLM_OPENAI__BASE_URL",
        "CODEORACLE_LLM_ANTHROPIC__API_KEY",
        "CODEORACLE_LLM_ANTHROPIC__MODEL",
        "CODEORACLE_LLM_VLLM__BASE_URL",
        "CODEORACLE_LLM_VLLM__MODEL",
        "CODEORACLE_LLM_OLLAMA__BASE_URL",
        "CODEORACLE_LLM_OLLAMA__MODEL",
        "CODEORACLE_LLM_MOCK__SCRIPTS_DIR",
        "CODEORACLE_LLM_MOCK__STRICT",
        "OPENAI_DEV_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_openai_api_key_required():
    with pytest.raises(ValidationError):
        OpenAIProviderConfig.model_validate({})
    with pytest.raises(ValidationError):
        OpenAIProviderConfig.model_validate({"api_key": ""})


def test_openai_api_key_is_secret_and_not_in_repr():
    """`api_key` is `SecretStr` so it never leaks via repr / str — the primary
    defense the log redaction processor cannot provide for nested configs
    (mirrors Plan 8's SourcebotConfig repr test)."""
    cfg = OpenAIProviderConfig.model_validate(
        {"api_key": "super-secret-do-not-log"}
    )
    assert isinstance(cfg.api_key, SecretStr)
    rendered = repr(cfg) + str(cfg.api_key)
    assert "super-secret-do-not-log" not in rendered
    assert cfg.api_key.get_secret_value() == "super-secret-do-not-log"


def test_openai_model_defaults_from_pinned_env(monkeypatch):
    monkeypatch.setenv("OPENAI_DEV_MODEL", "gpt-4o-test-pin")
    cfg = OpenAIProviderConfig.model_validate({"api_key": "sk-x"})
    assert cfg.model == "gpt-4o-test-pin"


def test_openai_model_default_when_pinned_env_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_DEV_MODEL", raising=False)
    cfg = OpenAIProviderConfig.model_validate({"api_key": "sk-x"})
    assert cfg.model == "gpt-4o-mini"


def test_anthropic_api_key_required():
    with pytest.raises(ValidationError):
        AnthropicProviderConfig.model_validate({})


def test_mock_config_defaults():
    cfg = MockLLMProviderConfig()
    assert cfg.strict is True
    assert "llm_scripts" in cfg.scripts_dir


def test_llmconfig_default_provider_openai_requires_openai_subconfig(
    monkeypatch,
):
    monkeypatch.delenv("CODEORACLE_LLM_OPENAI__API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig()
    assert "openai sub-config is None" in str(exc_info.value)


def test_llmconfig_openai_via_env(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("CODEORACLE_LLM_OPENAI__API_KEY", "sk-test")
    monkeypatch.setenv("CODEORACLE_LLM_OPENAI__MODEL", "gpt-4o-test")
    cfg = LLMConfig()
    assert cfg.provider == "openai"
    assert cfg.openai is not None
    assert cfg.openai.api_key.get_secret_value() == "sk-test"
    assert cfg.openai.model == "gpt-4o-test"


def test_llmconfig_anthropic_selected_but_no_subconfig_raises(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("CODEORACLE_LLM_ANTHROPIC__API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig()
    assert "anthropic sub-config is None" in str(exc_info.value)


def test_llmconfig_vllm_no_credentials_required(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "vllm")
    # vllm sub-config has all defaults — supplying empty triggers the validator
    # path
    monkeypatch.setenv(
        "CODEORACLE_LLM_VLLM__BASE_URL", "http://localhost:8000/v1"
    )
    cfg = LLMConfig()
    assert cfg.provider == "vllm"
    assert cfg.vllm is not None
    assert cfg.vllm.base_url == "http://localhost:8000/v1"


def test_llmconfig_mock_provider(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CODEORACLE_LLM_MOCK__STRICT", "false")
    cfg = LLMConfig()
    assert cfg.provider == "mock"
    assert cfg.mock is not None
    assert cfg.mock.strict is False


def test_llmconfig_invalid_provider_rejected(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "bogus")
    monkeypatch.setenv("CODEORACLE_LLM_OPENAI__API_KEY", "sk-x")
    with pytest.raises(ValidationError):
        LLMConfig()
