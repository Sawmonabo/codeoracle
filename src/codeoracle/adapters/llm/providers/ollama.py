"""OllamaProvider — Ollama's native /api/chat shape.

Like VLLMProvider, uses httpx directly. Ollama's `/api/chat` returns
JSON with `message`, `done`, `eval_count`, and `prompt_eval_count`.
Tool calling support depends on the model — Plan 4 includes it in the
request when tools is non-empty but doesn't validate model capability.

count_tokens uses the shared tokenizer_loader: it attempts the served model's
Hugging Face tokenizer in __init__ and encodes locally. An Ollama model id is a
TAG (e.g. "qwen2.5:32b"), not a Hub repo id, so the tokenizer resolves only
when CODEORACLE_LLM_OLLAMA__MODEL is set to a Hub-style id; otherwise
load_hf_tokenizer logs
a WARNING once and count_tokens degrades to a labeled len//4 heuristic.
"""

import json

import httpx
from tokenizers import Tokenizer

from codeoracle.adapters.llm.config import OllamaProviderConfig
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
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class OllamaProvider(LLMProvider):
    def __init__(self, config: OllamaProviderConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
        )
        # Exact local tokenizer if config.model resolves to a Hub repo id;
        # None otherwise (load_hf_tokenizer logs the WARNING once). See the
        # module docstring re: Ollama tags vs Hub ids.
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
            "stream": False,
        }
        if structured_output is not None:
            payload["format"] = pydantic_json_schema(
                structured_output, provider="OllamaProvider"
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
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Ollama HTTP error: {exc}") from exc

        body = response.json()
        message = body.get("message") or {}
        return ChatResponse(
            content=message.get("content") or "",
            usage=TokenUsage(
                input_tokens=body.get("prompt_eval_count", 0),
                output_tokens=body.get("eval_count", 0),
            ),
            finish_reason="stop" if body.get("done") else "length",
            tool_calls=[
                ToolCall(
                    id=tc.get("id") or f"call_{i}",
                    name=tc["function"]["name"],
                    arguments_json=json.dumps(
                        tc["function"].get("arguments", {})
                    ),
                )
                for i, tc in enumerate(message.get("tool_calls") or [])
            ],
        )

    def count_tokens(self, text: str) -> int:
        """
        Exact count via the served model's HF tokenizer when config.model is a
        Hub repo id (loaded in __init__); otherwise the labeled len//4
        fallback. Same path as vLLM (see tokenizer_loader)."""
        return count_tokens_with_fallback(self._tokenizer, text)

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain the underlying ``httpx.AsyncClient``."""
        await self._client.aclose()
