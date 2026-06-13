"""VLLMProvider — vLLM serves OpenAI-compatible /v1/chat/completions.

We use httpx.AsyncClient directly rather than the OpenAI SDK because:
  1. vLLM's auth is optional (defaults to "EMPTY" api_key).
  2. The OpenAI SDK adds retries/backoff we don't necessarily want against
     a local vLLM server (faster to fail-fast).
  3. Direct HTTP is simpler to reason about for the production path.

count_tokens loads the served model's Hugging Face tokenizer locally
(`tokenizers.Tokenizer.from_pretrained(config.model)`) and encodes with zero
per-call round-trip — the EXACT tokenizer the model uses. The tokenizer.json
is fetched once at construction (then disk-cached). If it can't load (offline,
gated repo, or config.model isn't a Hub repo id), the provider logs a WARNING
once and count_tokens degrades to a labeled `len//4` heuristic so the budget
gate keeps working on an approximation rather than crashing.
"""

from typing import cast

import httpx
from tokenizers import Tokenizer

from codeoracle.adapters.llm.config import VLLMProviderConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.llm.structured_output import pydantic_json_schema
from codeoracle.adapters.llm.tokenizer_loader import (
    count_tokens_with_fallback,
    load_hf_tokenizer,
)
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class VLLMProvider(LLMProvider):
    def __init__(self, config: VLLMProviderConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}"
            },
        )
        # Exact local tokenizer for the served model; None if it can't load
        # (load_hf_tokenizer logs the WARNING once). count_tokens falls back to
        # a labeled heuristic when None.
        self._tokenizer: Tokenizer | None = load_hf_tokenizer(config.model)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
        }
        if structured_output is not None:
            payload["guided_json"] = pydantic_json_schema(
                structured_output, provider="VLLMProvider"
            )
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters_schema,
                    },
                }
                for t in tools
            ]

        try:
            response = await self._client.post(
                "/chat/completions", json=payload
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"vLLM HTTP error: {exc}") from exc

        body = response.json()
        choice = body["choices"][0]
        usage = body.get("usage") or {}

        return ChatResponse(
            content=choice["message"].get("content") or "",
            usage=TokenUsage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
            finish_reason=_map_finish_reason(choice.get("finish_reason")),
            tool_calls=[
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments_json=tc["function"]["arguments"],
                )
                for tc in (choice["message"].get("tool_calls") or [])
            ],
        )

    def count_tokens(self, text: str) -> int:
        """
        Exact count via the served model's HF tokenizer (loaded in __init__).
        Falls back to a labeled len//4 heuristic if the tokenizer didn't load.
        """
        return count_tokens_with_fallback(self._tokenizer, text)

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain the underlying ``httpx.AsyncClient``."""
        await self._client.aclose()


def _map_finish_reason(reason: str | None) -> FinishReason:
    if reason in ("stop", "length", "tool_calls", "content_filter"):
        return cast(FinishReason, reason)
    return "stop"
