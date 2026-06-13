"""OpenAIProvider unit tests with mocked HTTP."""

import json
from typing import cast

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from codeoracle.adapters.llm.config import OpenAIProviderConfig
from codeoracle.adapters.llm.providers.openai import (
    OpenAIProvider,
    _to_openai_message,
)
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage, ToolCall


class _Intent(BaseModel):
    intent: str
    confidence: float
    note: str = ""


def _provider() -> OpenAIProvider:
    return OpenAIProvider(
        OpenAIProviderConfig(api_key=SecretStr("sk-test"), model="gpt-4o-mini")
    )


def test_openai_client_is_bounded_and_disables_sdk_retries() -> None:
    provider = OpenAIProvider(
        OpenAIProviderConfig(
            api_key=SecretStr("sk-test"),
            model="gpt-4o-mini",
            request_timeout_seconds=42.0,
        )
    )
    assert provider._client.max_retries == 0
    timeout = cast(httpx.Timeout, provider._client.timeout)
    assert timeout.read == 42.0
    assert timeout.connect == 5.0


async def test_openai_chat_happy_path() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-x",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "pong",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 1,
                        "total_tokens": 6,
                    },
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="ping")]
        )
        assert response.content == "pong"
        assert response.usage.input_tokens == 5
        assert response.usage.output_tokens == 1
        assert response.finish_reason == "stop"


async def test_openai_chat_wraps_sdk_error() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                500,
                json={
                    "error": {
                        "message": "internal server error",
                        "type": "server_error",
                    }
                },
            )
        )
        provider = _provider()
        with pytest.raises(LLMProviderError, match="OpenAI SDK error"):
            await provider.chat([ChatMessage(role="user", content="ping")])


async def test_openai_count_tokens_uses_tiktoken() -> None:
    provider = _provider()
    short = provider.count_tokens("hello")
    longer = provider.count_tokens(
        "the quick brown fox jumps over the lazy dog"
    )
    assert short >= 1
    assert longer > short


async def test_openai_count_tokens_unknown_model_falls_back() -> None:
    provider = OpenAIProvider(
        OpenAIProviderConfig(
            api_key=SecretStr("sk-test"), model="gpt-fictional-future"
        )
    )
    assert provider.count_tokens("hello") > 0


async def test_openai_model_name() -> None:
    assert _provider().model_name() == "gpt-4o-mini"


async def test_openai_max_context_tokens_from_config() -> None:
    provider = OpenAIProvider(
        OpenAIProviderConfig(
            api_key=SecretStr("sk-test"),
            model="gpt-4o-mini",
            max_context_tokens=16_000,
        )
    )
    assert provider.max_context_tokens() == 16_000


async def test_openai_chat_with_tool_calls_response() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-x",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "search",
                                            "arguments": '{"q":"x"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 8,
                        "total_tokens": 13,
                    },
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="search for x")]
        )
        assert response.finish_reason == "tool_calls"
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "search"
        assert response.tool_calls[0].arguments_json == '{"q":"x"}'


async def test_openai_structured_output_sends_strict_json_schema() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-x",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": (
                                    '{"intent":"explain",'
                                    '"confidence":0.9,"note":""}'
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 9,
                        "total_tokens": 14,
                    },
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="classify")],
            structured_output=_Intent,
        )
        assert response.content == (
            '{"intent":"explain","confidence":0.9,"note":""}'
        )
        sent = json.loads(route.calls.last.request.content)
        response_format = sent["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "_Intent"
        assert response_format["json_schema"]["strict"] is True
        schema = response_format["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"intent", "confidence", "note"}


async def test_openai_structured_output_rejects_non_pydantic_type() -> None:
    provider = _provider()
    with pytest.raises(LLMProviderError, match=r"pydantic\.BaseModel"):
        await provider.chat(
            [ChatMessage(role="user", content="x")],
            structured_output=dict,
        )


def test_to_openai_message_omits_tool_calls() -> None:
    message = ChatMessage(
        role="assistant",
        content="calling",
        tool_calls=[
            ToolCall(id="call_1", name="search", arguments_json='{"q":"x"}')
        ],
    )
    wire = _to_openai_message(message)
    assert wire == {"role": "assistant", "content": "calling"}
    assert "tool_calls" not in wire
