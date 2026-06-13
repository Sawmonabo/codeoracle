"""Context budget enforcement — refuses LLM calls that exceed the platform-wide
token cap. Lives in services/ (not adapters/) because the cap is a domain
rule: the platform's safety contract says no single LLM call exceeds N tokens.

Every concrete LLMProvider implementation (codeoracle.adapters.llm.providers.*)
calls `ContextBudgetService.check(messages, self.count_tokens)` before sending.
Refusal happens upstream of the SDK call.
"""

from collections.abc import Callable

from codeoracle.core.exceptions import CodeOracleError
from codeoracle.models.llm import ChatMessage


class ContextBudgetExceededError(CodeOracleError):
    """Raised when a prepared message list exceeds the configured token cap.

    Subclass of CodeOracleError so all platform exceptions share a single
    ancestor.
    """


ContextBudgetExceeded = ContextBudgetExceededError


class ContextBudgetService:
    """Enforces the platform-wide token cap on outgoing LLM requests.

    Default 24_000 tokens — the spec §3.6 settled this as the safe operating
    window for the production target (Qwen3.6 served via vLLM). Construction
    point is the composition root (api/deps.py for the API process, workers/
    _base/wiring.py for worker processes); each takes its own instance.
    """

    def __init__(self, max_tokens: int = 24_000) -> None:
        if max_tokens <= 0:
            raise ValueError(
                "ContextBudgetService.max_tokens must be > 0, "
                f"got {max_tokens}"
            )
        self._max_tokens = max_tokens

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def check(
        self,
        messages: list[ChatMessage],
        token_counter: Callable[[str], int],
    ) -> int:
        """Sum token-counts of `messages.content` using `token_counter`.

        Returns the total. Raises ContextBudgetExceeded if total > max_tokens.

        `token_counter` is the provider's `count_tokens` method (passed in
        rather than imported because each provider counts differently —
        OpenAI uses tiktoken, vLLM approximates, etc.). This keeps
        ContextBudgetService provider-agnostic.
        """
        total = sum(token_counter(m.content) for m in messages)
        if total > self._max_tokens:
            raise ContextBudgetExceeded(
                f"prepared request totals {total} tokens, exceeds max "
                f"{self._max_tokens}"
            )
        return total
