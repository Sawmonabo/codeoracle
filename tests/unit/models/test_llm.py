"""LLM model tests."""

import pytest
from pydantic import ValidationError

from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


def test_chat_message_minimum():
    m = ChatMessage(role="user", content="hello")
    assert m.role == "user"
    assert m.tool_calls == []


def test_chat_message_rejects_bad_role():
    with pytest.raises(ValidationError):
        ChatMessage.model_validate({"role": "root", "content": "hello"})


def test_token_usage_negative_rejected():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1, output_tokens=10)


def test_token_usage_total():
    u = TokenUsage(input_tokens=10, output_tokens=20)
    assert u.total_tokens == 30


def test_token_usage_is_frozen():
    u = TokenUsage(input_tokens=10, output_tokens=20)
    with pytest.raises(ValidationError):
        u.input_tokens = 99


def test_chat_response_construction():
    resp = ChatResponse(
        content="pong",
        usage=TokenUsage(input_tokens=5, output_tokens=2),
        finish_reason="stop",
    )
    assert resp.tool_calls == []


def test_tool_definition_name_constraints():
    with pytest.raises(ValidationError):
        ToolDefinition(name="", description="x", parameters_schema={})


def test_tool_call_round_trip():
    tc = ToolCall(id="call_abc", name="search", arguments_json='{"q":"x"}')
    assert tc.model_dump()["arguments_json"] == '{"q":"x"}'
