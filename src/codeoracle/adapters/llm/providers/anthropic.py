"""AnthropicProvider — Anthropic Messages API.

Constructs AsyncAnthropic once in __init__ for chat(). count_tokens uses
Anthropic's REAL token-counting endpoint — there is no local Anthropic
tokenizer, so an exact count requires the API. Because the ABC's
``count_tokens`` is synchronous, we hold a SECOND, synchronous
``anthropic.Anthropic`` client purely for ``messages.count_tokens(...)``
(the sync client makes a blocking HTTPS call; no async bridge is needed).
Counts are memoized by content hash — prompt templates are stable, so the
set of distinct strings is small and the network cost amortizes away. If the
count API errors (offline / auth / rate-limit), we log a WARNING and fall back
to a labeled ``len(text) // 4`` heuristic so a transient counting failure
degrades the budget gate rather than crashing the chat() call.
"""

import hashlib
import json
from typing import Any, cast

import anthropic
import httpx
from anthropic import Anthropic, AsyncAnthropic

from codeoracle.adapters.llm.config import AnthropicProviderConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.llm.structured_output import pydantic_json_schema
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.core.logging import get_logger
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class AnthropicProvider(LLMProvider):
    def __init__(self, config: AnthropicProviderConfig) -> None:
        self._config = config
        # Bound every call (D6-2; see OpenAIProvider): the SDK's own long
        # default x internal retries is not a platform-reasoned ceiling, and
        # retry is the worker RetryPolicy's job — disable the SDK multiplier.
        self._client = AsyncAnthropic(
            api_key=config.api_key.get_secret_value(),
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            max_retries=0,
        )
        # Separate SYNCHRONOUS client used ONLY by count_tokens(): the ABC's
        # count_tokens is sync, Anthropic has no local tokenizer, and the count
        # API is the only exact counter. The sync client blocks on its own
        # HTTPS call — no async bridge needed. Same bounded-timeout / no-retry
        # policy as the async client (D6-2).
        self._sync_client = Anthropic(
            api_key=config.api_key.get_secret_value(),
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            max_retries=0,
        )
        self._token_cache: dict[str, int] = {}
        self._logger = get_logger(__name__)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        system_prompt, user_assistant_messages = _split_system(messages)
        request_tools = [_to_anthropic_tool(t) for t in (tools or [])]
        output_tool_name: str | None = None
        if structured_output is not None:
            output_tool_name = _structured_tool_name(structured_output)
            request_tools.append(
                {
                    "name": output_tool_name,
                    "description": (
                        "Return the final response as arguments matching "
                        "this JSON schema."
                    ),
                    "input_schema": pydantic_json_schema(
                        structured_output, provider="AnthropicProvider"
                    ),
                }
            )

        try:
            request: dict[str, Any] = {
                "model": self._config.model,
                "system": system_prompt or anthropic.NOT_GIVEN,
                "messages": [
                    _to_anthropic_message(m) for m in user_assistant_messages
                ],
                "tools": request_tools or anthropic.NOT_GIVEN,
                "max_tokens": self._config.max_output_tokens,
            }
            if output_tool_name is not None:
                request["tool_choice"] = {
                    "type": "tool",
                    "name": output_tool_name,
                }
            response = await self._client.messages.create(**cast(Any, request))
        except anthropic.AnthropicError as exc:
            raise LLMProviderError(f"Anthropic SDK error: {exc}") from exc

        content_text = ""
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                if block.name == output_tool_name:
                    content_text = json.dumps(block.input)
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments_json=json.dumps(block.input),
                    )
                )

        return ChatResponse(
            content=content_text,
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            finish_reason=_map_finish_reason(response.stop_reason),
            tool_calls=tool_calls,
        )

    def count_tokens(self, text: str) -> int:
        """Exact Anthropic token count via the count-tokens API, memoized by
        content hash. Falls back to a labeled len//4 heuristic ONLY if the API
        errors (logged WARNING) so a transient counting failure degrades the
        budget gate instead of crashing the call."""
        if not text:
            return 0
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._token_cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._sync_client.messages.count_tokens(
                model=self._config.model,
                messages=[{"role": "user", "content": text}],
            )
            count = result.input_tokens
        except anthropic.AnthropicError as exc:
            # Last-resort fallback — NOT silent: surfaced so the operator sees
            # the budget gate is running on an approximation, not the real
            # count.
            count = max(1, len(text) // 4)
            self._logger.warning(
                "anthropic_count_tokens_api_failed_using_heuristic",
                error=str(exc),
                model=self._config.model,
                heuristic="len//4",
                approx_tokens=count,
            )
        self._token_cache[key] = count
        return count

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain both Anthropic clients (Plan 10 Task 11 calls in lifespan
        shutdown). The async client is awaited; the sync count-tokens client is
        closed synchronously (it exposes a blocking ``close()``, not a coro).
        """
        await self._client.close()
        self._sync_client.close()


def _split_system(
    messages: list[ChatMessage],
) -> tuple[str, list[ChatMessage]]:
    """
    Anthropic API takes 'system' as a top-level param, not a message role.
    """
    system_parts: list[str] = []
    rest: list[ChatMessage] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        else:
            rest.append(m)
    return ("\n\n".join(system_parts), rest)


def _to_anthropic_message(m: ChatMessage) -> dict[str, object]:
    """Serialize a ChatMessage into Anthropic's wire shape.

    Plan 4 scope: flat role + string-content. Sufficient for the
    single-turn smoke test (`pong` round-trip).

    INTENTIONALLY OMITS assistant `m.tool_calls` — the field is kept in the
    ChatMessage model (spec §5.2) for the eventual multi-turn agent loop, but
    Plan 4 is a single-shot slice and never replays a prior assistant turn.
    Multi-turn flows that feed back a prior assistant turn with tool uses need
    Anthropic's content-block array shape: [{"type": "text", "text": "..."},
         {"type": "tool_use", "id": "...", "name": "...", "input": {...}}]
    OWNER: Plan 10 (the agent process) introduces the multi-turn loop and will
    replace the flat-string branch with the content-block branch when
    ``m.tool_calls`` is non-empty. The omission is pinned by
    ``test_to_anthropic_message_omits_tool_calls`` so a future edit that starts
    emitting them is a conscious, test-breaking change, not an accident.
    """
    return {"role": m.role, "content": m.content}


def _to_anthropic_tool(t: ToolDefinition) -> dict[str, object]:
    return {
        "name": t.name,
        "description": t.description,
        "input_schema": t.parameters_schema,
    }


def _structured_tool_name(target: type) -> str:
    raw = f"return_{target.__name__}"
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    return safe[:64] or "return_structured_output"


def _map_finish_reason(reason: str | None) -> FinishReason:
    """Anthropic: 'end_turn' | 'max_tokens' | 'stop_sequence' | 'tool_use'."""
    mapping: dict[str, FinishReason] = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
    }
    return mapping.get(reason or "", "stop")
