"""
BudgetedLLMProvider — service-layer budget enforcement composed around a raw
provider.

Raw providers (adapters/llm/providers/) are pure I/O: they hold no reference to
the platform's token policy. ContextBudgetService is a domain rule (services/),
so the budget is enforced by WRAPPING a provider at the service layer — never
by an upward import from adapters/ into services/ (NFR D7a-1, memory
`strict-layer-purity`). `build_budgeted_provider` is the single blessed
construction path; composition roots (workers/_base/wiring.py, the agent's
FastAPI lifespan) call it once.
"""

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.factory import LLMProviderFactory
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition
from codeoracle.services.context_budget import ContextBudgetService


class BudgetedLLMProvider(LLMProvider):
    """Wraps a raw LLMProvider, enforcing ContextBudgetService.check() before
    each chat() delegates to the inner provider. Every other LLMProvider method
    delegates unchanged."""

    def __init__(
        self, inner: LLMProvider, budget: ContextBudgetService
    ) -> None:
        self._inner = inner
        self._budget = budget

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        # Enforce the platform token cap before the inner provider's network
        # call, counting with the inner provider's OWN tokenizer (each model
        # counts differently). Raises ContextBudgetExceeded.
        self._budget.check(messages, self._inner.count_tokens)
        return await self._inner.chat(
            messages, tools=tools, structured_output=structured_output
        )

    def count_tokens(self, text: str) -> int:
        return self._inner.count_tokens(text)

    def model_name(self) -> str:
        return self._inner.model_name()

    def max_context_tokens(self) -> int:
        return self._inner.max_context_tokens()

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_budgeted_provider(
    config: LLMConfig, budget: ContextBudgetService
) -> LLMProvider:
    """The single blessed path to a production LLM provider: construct the raw
    provider via LLMProviderFactory, then wrap it in BudgetedLLMProvider so no
    composition root can accidentally hold an unbounded provider (fail-safe).
    """
    return BudgetedLLMProvider(LLMProviderFactory(config).create(), budget)
