"""LLM call types — provider-agnostic.

Every LLMProvider implementation (Plan 4) accepts ChatMessage[] and returns
ChatResponse. Tool calls are modeled here (not OpenAI-specific) so the
abstraction does not leak vendor schema.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "length", "tool_calls", "content_filter"]


class ToolCall(BaseModel):
    """An LLM-requested tool invocation. Mirrors the OpenAI / Anthropic surface
    without binding to either's JSON shape."""

    id: str
    name: str
    arguments_json: str


class ChatMessage(BaseModel):
    role: Role
    content: str
    template_id: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ChatResponse(BaseModel):
    content: str
    usage: TokenUsage
    finish_reason: FinishReason
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    """A tool the LLM may call. Schema is JSON-Schema (the dialect both
    OpenAI function-calling and Anthropic tool-use accept)."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    description: str
    parameters_schema: dict[str, object]
    """JSON-Schema document. Heterogeneous nested types — `object` is the
    honest annotation; `Any` would lose type-checker signal entirely."""
