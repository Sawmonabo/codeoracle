"""Plan 4 Task 9: AnthropicProvider unit tests (respx-mocked)."""

import json

import httpx
import pytest
import respx

from codeoracle.adapters.llm.config import AnthropicProviderConfig
from codeoracle.adapters.llm.providers.anthropic import (
    AnthropicProvider,
    _to_anthropic_message,
)
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage, ToolCall


def _provider() -> AnthropicProvider:
    return AnthropicProvider(
        AnthropicProviderConfig.model_validate(
            {"api_key": "sk-ant-test", "model": "claude-opus-4-8"}
        ),
    )


def test_anthropic_client_is_bounded_and_disables_sdk_retries():
    """D6-2 (see OpenAIProvider): bound every call, disable the hidden SDK
    retry multiplier — retry is the worker RetryPolicy's job.
    (Asserts the constructed client's effective settings.)"""
    p = AnthropicProvider(
        AnthropicProviderConfig.model_validate(
            {
                "api_key": "sk-ant-test",
                "model": "claude-opus-4-8",
                "request_timeout_seconds": 99.0,
            }
        ),
    )
    assert p._client.max_retries == 0
    assert isinstance(p._client.timeout, httpx.Timeout)
    assert p._client.timeout.read == 99.0
    assert p._client.timeout.connect == 5.0


async def test_anthropic_chat_happy_path():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg_01",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "pong"}],
                    "model": "claude-opus-4-8",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="ping")]
        )
        assert response.content == "pong"
        assert response.usage.input_tokens == 5
        assert response.finish_reason == "stop"


async def test_anthropic_wraps_sdk_error():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                500,
                json={
                    "type": "error",
                    "error": {"type": "api_error", "message": "boom"},
                },
            ),
        )
        provider = _provider()
        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat([ChatMessage(role="user", content="ping")])
        assert "Anthropic SDK error" in str(exc_info.value)


async def test_anthropic_splits_system_from_messages():
    """
    System messages must go to the top-level `system` param, not into
    messages[].
    """
    captured = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "model": "claude-opus-4-8",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=capture
        )
        provider = _provider()
        await provider.chat(
            [
                ChatMessage(role="system", content="be helpful"),
                ChatMessage(role="user", content="hi"),
            ]
        )

    body = captured["body"]
    assert body["system"] == "be helpful"
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_anthropic_max_tokens_per_config():
    provider = AnthropicProvider(
        AnthropicProviderConfig.model_validate(
            {
                "api_key": "sk-ant-test",
                "model": "claude-opus-4-8",
                "max_context_tokens": 8192,
            }
        ),
    )
    assert provider.max_context_tokens() == 8192


async def test_anthropic_count_tokens_uses_api_and_caches():
    """count_tokens calls the real count-tokens API (via the sync client) and
    memoizes by content hash — a second identical call hits the cache, not the
    network. Empty string short-circuits to 0 with no API call."""
    route = respx.post(
        "https://api.anthropic.com/v1/messages/count_tokens"
    ).mock(return_value=httpx.Response(200, json={"input_tokens": 7}))
    with respx.mock(assert_all_called=True):
        provider = _provider()
        assert provider.count_tokens("") == 0  # no API call
        assert provider.count_tokens("hello") == 7  # 1st call → API
        assert provider.count_tokens("hello") == 7  # 2nd call → cache
    assert route.call_count == 1, (
        "second identical count must be served from cache"
    )


async def test_anthropic_count_tokens_falls_back_to_heuristic_on_api_error():
    """If the count-tokens API errors, count_tokens degrades to a labeled
    len//4 heuristic (logged WARNING) instead of crashing the budget check."""
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages/count_tokens").mock(
            return_value=httpx.Response(
                503,
                json={
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "busy"},
                },
            ),
        )
        provider = _provider()
        # "hello world!" is 12 chars → 12 // 4 == 3 via the heuristic fallback.
        assert provider.count_tokens("hello world!") == 3


def test_to_anthropic_message_omits_tool_calls():
    """D-4.9: the outbound converter INTENTIONALLY drops assistant tool_calls
    (single-shot slice; multi-turn replay with content-block tool_use is owned
    by Plan 10). This pins the omission so a future edit that begins emitting
    the content-block array is a conscious, test-breaking change. The wire dict
    stays flat role + string-content with no tool_use blocks."""
    msg = ChatMessage(
        role="assistant",
        content="calling",
        tool_calls=[
            ToolCall(id="toolu_1", name="search", arguments_json='{"q":"x"}')
        ],
    )
    wire = _to_anthropic_message(msg)
    assert wire == {"role": "assistant", "content": "calling"}
    assert isinstance(
        wire["content"], str
    )  # flat string, not a content-block list
