"""OpenAIProvider — the dev-default LLM provider."""

from typing import Any, cast

import httpx
import openai
import tiktoken
from openai import AsyncOpenAI
from pydantic import BaseModel

from codeoracle.adapters.llm.config import OpenAIProviderConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class OpenAIProvider(LLMProvider):
    def __init__(self, config: OpenAIProviderConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            max_retries=0,
        )
        try:
            self._encoding = tiktoken.encoding_for_model(config.model)
        except KeyError:
            self._encoding = tiktoken.get_encoding("o200k_base")

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        payload = {
            "model": self._config.model,
            "messages": [_to_openai_message(m) for m in messages],
            "tools": [_to_openai_tool(t) for t in (tools or [])] or None,
            "response_format": (
                _to_response_format(structured_output)
                if structured_output is not None
                else openai.NOT_GIVEN
            ),
        }
        try:
            response = await self._client.chat.completions.create(
                **cast(Any, payload)
            )
        except openai.OpenAIError as exc:
            raise LLMProviderError(f"OpenAI SDK error: {exc}") from exc

        choice = response.choices[0]
        usage = response.usage
        return ChatResponse(
            content=choice.message.content or "",
            usage=TokenUsage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
            finish_reason=_map_finish_reason(choice.finish_reason),
            tool_calls=[
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    arguments_json=tool_call.function.arguments,
                )
                for tool_call in (choice.message.tool_calls or [])
            ],
        )

    def count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain the AsyncOpenAI HTTP client."""
        await self._client.close()


def _to_openai_message(message: ChatMessage) -> dict[str, object]:
    wire: dict[str, object] = {
        "role": message.role,
        "content": message.content,
    }
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _to_openai_tool(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        },
    }


def _to_response_format(target: type) -> dict[str, object]:
    if not (isinstance(target, type) and issubclass(target, BaseModel)):
        raise LLMProviderError(
            "OpenAI structured_output requires a pydantic.BaseModel "
            f"subclass; got {target!r}"
        )

    schema = target.model_json_schema()
    _enforce_strict_object_schema(schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": target.__name__,
            "schema": schema,
            "strict": True,
        },
    }


def _enforce_strict_object_schema(node: object) -> None:
    if isinstance(node, dict):
        mapping = cast(dict[str, Any], node)
        if mapping.get("type") == "object":
            mapping["additionalProperties"] = False
            properties = mapping.get("properties")
            if isinstance(properties, dict):
                mapping["required"] = list(properties.keys())
        for value in mapping.values():
            _enforce_strict_object_schema(value)
    elif isinstance(node, list):
        for item in node:
            _enforce_strict_object_schema(item)


def _map_finish_reason(reason: str | None) -> FinishReason:
    if reason == "function_call":
        return "tool_calls"
    if reason in ("stop", "length", "tool_calls", "content_filter"):
        return cast(FinishReason, reason)
    return "stop"
