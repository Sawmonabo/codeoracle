"""LLMProviderFactory — constructs the RAW provider for LLMConfig.provider.

NOT the blessed construction path: a bare factory product is unbounded.
Production code builds providers through
`codeoracle.services.llm.build_budgeted_provider`, which wraps this factory's
output in BudgetedLLMProvider (services/llm/) before any composition root sees
it. NEVER instantiated inside a service — services receive a constructed,
budget-wrapped LLMProvider via DI.

Provider modules are imported at module load so factory construction stays
simple and visible to static checks.
"""

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.llm.providers.anthropic import AnthropicProvider
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.adapters.llm.providers.ollama import OllamaProvider
from codeoracle.adapters.llm.providers.openai import OpenAIProvider
from codeoracle.adapters.llm.providers.vllm import VLLMProvider


class LLMProviderFactory:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def create(self) -> LLMProvider:
        """Construct the provider matching `LLMConfig.provider`.

        The `if X is None: raise` form is used instead of `assert X is not
        None`
        because asserts are stripped under `python -O`, and the None-check is
        load-bearing (it's the second line of defense after LLMConfig's
        cross-validator; ty also narrows correctly with explicit checks).
        """
        match self._config.provider:
            case "openai":
                return self._create_openai()
            case "anthropic":
                return self._create_anthropic()
            case "vllm":
                return self._create_vllm()
            case "ollama":
                return self._create_ollama()
            case "mock":
                return self._create_mock()
            case _:
                # Defensive: `ProviderName` is a `Literal[...]` so the
                # cross-validator should keep us inside the five known cases
                # at config-load time. If someone extends `ProviderName`
                # without adding a case branch above, this raises loudly
                # rather than falling off the end and returning `None`.
                # Also makes the match exhaustive for `ty`.
                raise ValueError(
                    f"Unsupported provider {self._config.provider!r}. "
                    "Extend ProviderName AND add a case branch in "
                    "LLMProviderFactory.create() together."
                )

    def _create_openai(self) -> OpenAIProvider:
        if self._config.openai is None:
            raise ValueError(
                "LLMConfig.openai is None despite provider='openai'; "
                "the cross-validator should have caught this."
            )
        return OpenAIProvider(self._config.openai)

    def _create_anthropic(self) -> AnthropicProvider:
        if self._config.anthropic is None:
            raise ValueError(
                "LLMConfig.anthropic is None despite provider='anthropic'."
            )
        return AnthropicProvider(self._config.anthropic)

    def _create_vllm(self) -> VLLMProvider:
        if self._config.vllm is None:
            raise ValueError("LLMConfig.vllm is None despite provider='vllm'.")
        return VLLMProvider(self._config.vllm)

    def _create_ollama(self) -> OllamaProvider:
        if self._config.ollama is None:
            raise ValueError(
                "LLMConfig.ollama is None despite provider='ollama'."
            )
        return OllamaProvider(self._config.ollama)

    def _create_mock(self) -> MockLLMProvider:
        if self._config.mock is None:
            raise ValueError("LLMConfig.mock is None despite provider='mock'.")
        return MockLLMProvider(self._config.mock)
