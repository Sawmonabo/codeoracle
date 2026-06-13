"""
LLM provider configuration. Reads env vars CODEORACLE_LLM_* (double-underscore
nested delimiter matches Plan 3's core/config.py convention).

Env shape examples (all use CODEORACLE_LLM_ prefix):
  CODEORACLE_LLM_PROVIDER=openai          → LLMConfig.provider == "openai"
  CODEORACLE_LLM_OPENAI__API_KEY=sk-...   →
  LLMConfig.openai.api_key.get_secret_value() == "sk-..."
  CODEORACLE_LLM_OPENAI__MODEL=gpt-4o-mini → LLMConfig.openai.model ==
  "gpt-4o-mini"

Cross-validator (`_check_selected_provider_config_present`) raises at Settings
construction if CODEORACLE_LLM_PROVIDER=openai but
CODEORACLE_LLM_OPENAI__API_KEY is unset.
This prevents the factory from blowing up at first-use (which would be
non-deterministic — depends on which service tries to chat first).
"""

import os
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_openai_model() -> str:
    """
    Default to pinned.env's OPENAI_DEV_MODEL if set in env, else gpt-4o-mini.

    pinned.env is sourced before tests / dev shells / docker-compose. If
    OPENAI_DEV_MODEL is not in env at Settings construction time, fall back
    to gpt-4o-mini (the value Plan 2 baselined). Production overrides via
    CODEORACLE_LLM_OPENAI__MODEL.
    """
    return os.environ.get("OPENAI_DEV_MODEL", "gpt-4o-mini")


class OpenAIProviderConfig(BaseModel):
    api_key: SecretStr = Field(min_length=1)
    """OpenAI API key.

    `SecretStr` prevents repr, dump, exception, or OTel span leakage when this
    config is nested somewhere the name-keyed log redaction processor cannot
    reach. `.get_secret_value()` is used only at the SDK call site.
    """
    model: str = Field(default_factory=_default_openai_model)
    base_url: str | None = None
    """For OpenAI-compatible endpoints (e.g., a local proxy). None means
    the SDK's default endpoint."""
    max_context_tokens: int = Field(default=128_000, ge=1)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    """Total per-call read timeout; connect timeout is a fixed 5s."""


class AnthropicProviderConfig(BaseModel):
    api_key: SecretStr = Field(min_length=1)
    model: str = Field(default="claude-opus-4-8", min_length=1)
    max_output_tokens: int = Field(
        default=4096, ge=1
    )  # Anthropic requires max_tokens on every request
    max_context_tokens: int = Field(default=200_000, ge=1)
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    """Total per-call read timeout; connect timeout is a fixed 5s."""


class VLLMProviderConfig(BaseModel):
    base_url: str = Field(default="http://vllm:8000/v1", min_length=1)
    """vLLM serves the OpenAI-compatible /v1/chat/completions shape."""
    model: str = Field(default="Qwen/Qwen3.6-32B", min_length=1)
    max_context_tokens: int = Field(default=32_768, ge=1)
    api_key: SecretStr = Field(default=SecretStr("EMPTY"), min_length=1)
    """vLLM's OpenAI-compatible server requires SOME api_key string; "EMPTY"
    is the convention for unauthenticated local servers. `SecretStr` for a
    uniform secret-typing convention across providers (Plan 8 parity)."""
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    """Total per-call read timeout (connect is a fixed 5s)."""


class OllamaProviderConfig(BaseModel):
    base_url: str = Field(default="http://ollama:11434", min_length=1)
    model: str = Field(default="qwen2.5:32b", min_length=1)
    max_context_tokens: int = Field(default=32_768, ge=1)
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    """Total per-call read timeout (connect is a fixed 5s); 120s covers a local
    model's cold-start latency."""


class MockLLMProviderConfig(BaseModel):
    scripts_dir: str = Field(
        default="tests/fixtures/llm_scripts", min_length=1
    )
    strict: bool = True
    """If True, unknown request fingerprints raise. If False, return a
    canned 'no fixture' response. Default True so test fixture rot is
    surfaced immediately."""


ProviderName = Literal["openai", "anthropic", "vllm", "ollama", "mock"]


class LLMConfig(BaseSettings):
    """Provider selection + per-provider config. Read from env at process boot.

    The factory at codeoracle.adapters.llm.factory uses this to construct a
    concrete LLMProvider. The cross-validator ensures the selected
    provider's sub-config is non-None — failing at Settings construction,
    not at the first chat() call.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_LLM_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: ProviderName = "openai"
    openai: OpenAIProviderConfig | None = None
    anthropic: AnthropicProviderConfig | None = None
    vllm: VLLMProviderConfig | None = None
    ollama: OllamaProviderConfig | None = None
    mock: MockLLMProviderConfig | None = None

    @model_validator(mode="after")
    def _check_selected_provider_config_present(self) -> LLMConfig:
        selected: BaseModel | None = getattr(self, self.provider)
        if selected is None:
            provider_env = f"CODEORACLE_LLM_{self.provider.upper()}__API_KEY"
            raise ValueError(
                f"LLMConfig.provider='{self.provider}' but "
                f"{self.provider} sub-config is None. Set "
                f"CODEORACLE_LLM_{self.provider.upper()}__* env vars (e.g., "
                f"{provider_env} for credential-bearing providers)."
            )
        return self
