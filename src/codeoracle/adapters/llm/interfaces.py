"""LLMProvider ABC — the single seam all platform code uses to call an LLM.

No concrete provider SDK is imported here. Each concrete provider lives
under providers/ and imports its SDK locally (the only filesystem location
where direct SDK imports are allowed, enforced by ruff TID251 per-file-ignore).

`async def chat()` accepts a prepared ChatMessage list + optional tools +
optional structured_output type, and returns a ChatResponse. The provider
implementations are responsible for:
  1. Calling ContextBudgetService.check(messages, self.count_tokens) BEFORE
     send.
  2. Translating to provider-native request shape, calling the async client.
  3. Translating provider response back to the platform's ChatResponse type.
  4. Wrapping SDK exceptions in LLMProviderError.

Streaming is intentionally NOT in this ABC. A future plan may add
`async def stream(...) -> AsyncIterator[ChatResponseChunk]` as a separate
abstract method; that's an additive change that doesn't break existing callers.
"""

from abc import ABC, abstractmethod

from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition


class LLMProvider(ABC):
    """Provider-agnostic contract for sending a chat turn to an LLM.

    Lifecycle note: concrete providers (OpenAI / Anthropic / vLLM / Ollama)
    construct async HTTP clients in ``__init__`` and reuse them across calls.
    The ABC ships ``async def aclose(self) -> None`` with a default no-op
    body (below); providers with persistent connections override it to drain
    their client (see each provider's ``aclose()`` docstring for the specific
    drain call). Plan 10's agent process (Task 11) registers ``aclose()`` in
    its FastAPI lifespan shutdown so HTTP/SDK connections drain cleanly
    before process exit; Plan 11 + Plan 12 inherit the pattern when written.
    Tests that need manual drain semantics can call ``await provider.aclose()``
    directly.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        """Send `messages` (and optional tool definitions / structured-output
        target type) to the underlying LLM. Returns a fully populated
        ChatResponse (content + usage + finish_reason + tool_calls).

        Implementations MUST wrap SDK exceptions in LLMProviderError. Budget
        enforcement is NOT a provider concern: the BudgetedLLMProvider
        decorator
        (services/llm/) calls ContextBudgetService.check() around this method,
        so a raw provider MUST NOT import or reference the budget (NFR D7a-1).
        """

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Synchronously return the token count of `text` using the EXACT
        tokenizer for this provider's model wherever one is available.

        Used by ContextBudgetService.check() (driven by the BudgetedLLMProvider
        decorator). Implementations:
          - OpenAI: tiktoken with the model-correct encoding
            (encoding_for_model; o200k_base fallback for unknown 4o-class ids).
          - Anthropic: the real count-tokens API via a synchronous client,
            memoized by content hash (no local Anthropic tokenizer exists).
          - vLLM / Ollama: the served model's Hugging Face tokenizer loaded
            locally (tokenizer_loader), encoding with zero per-call round-trip.
          - Mock: deterministic stub (len(text) // 4).
        A labeled `len // 4` heuristic is the LOGGED last-resort fallback only
        when the exact path is unavailable (count API errors; HF tokenizer
        can't load — e.g. an Ollama tag isn't a Hub repo id). Never silent.
        """

    @abstractmethod
    def model_name(self) -> str:
        """The configured model identifier (e.g., 'gpt-4o-mini',
        'claude-opus-4-8', 'Qwen/Qwen3.6-32B'). Used for logging."""

    @abstractmethod
    def max_context_tokens(self) -> int:
        """Hard ceiling reported by the model. Distinct from
        ContextBudgetService.max_tokens (the platform's policy cap, which
        is typically lower than the model's hard ceiling)."""

    async def aclose(self) -> None:
        """Drain underlying HTTP/SDK clients.

        Default no-op for providers without persistent connections (mock,
        test-stub subclasses). Subclasses that hold a persistent client
        override this to drain it — see each provider's `aclose()` docstring
        for the specific call (`close()` on Stainless-wrapped SDKs vs.
        `aclose()` on raw `httpx.AsyncClient`). The agent process (Plan 10
        Task 11) calls this in its FastAPI lifespan shutdown handler.
        """
        return
