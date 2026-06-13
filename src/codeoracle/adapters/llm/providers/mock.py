"""MockLLMProvider — test-only provider that returns scripted responses keyed
by request fingerprint.

Behavior:
  - On __init__: loads ALL .yaml files under scripts_dir into an
    in-memory dict[fingerprint, ChatResponse].
  - On chat(): computes the fingerprint of (messages, tools), looks it up,
    returns the canned ChatResponse.
  - On unknown fingerprint with strict=True (default): raises
    LLMProviderError. With strict=False: returns a default "no fixture"
    response (content=""; usage zero; finish_reason="stop").

Token-counting stub: returns len(text) // 4. Deterministic, provider-free.
"""

from pathlib import Path

import yaml

from codeoracle.adapters.llm.config import MockLLMProviderConfig
from codeoracle.adapters.llm.fingerprint import request_fingerprint
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class MockLLMProvider(LLMProvider):
    """
    Deterministic provider for unit tests. NEVER used in production paths.
    """

    def __init__(self, config: MockLLMProviderConfig) -> None:
        self._config = config
        self._scripts: dict[str, ChatResponse] = self._load_scripts(
            config.scripts_dir
        )

    @staticmethod
    def _load_scripts(scripts_dir: str) -> dict[str, ChatResponse]:
        out: dict[str, ChatResponse] = {}
        root = Path(scripts_dir)
        if not root.is_dir():
            return (
                out  # no fixtures yet — strict mode will raise on first chat()
            )
        for yaml_file in sorted(root.glob("*.yaml")):
            entries = yaml.safe_load(yaml_file.read_text()) or []
            for entry in entries:
                fp = entry["request_fingerprint"]
                raw = entry["response"]
                response = ChatResponse(
                    content=raw["content"],
                    usage=TokenUsage(**raw["usage"]),
                    finish_reason=raw["finish_reason"],
                    tool_calls=[
                        ToolCall(**tc) for tc in raw.get("tool_calls", [])
                    ],
                )
                if fp in out:
                    raise LLMProviderError(
                        f"duplicate request_fingerprint '{fp}' in {yaml_file}"
                    )
                out[fp] = response
        return out

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        # Fingerprint the FULL spec §5.7 contract — model + structured_output
        # included so a request to a different mock-model identifier or
        # expecting a different schema resolves to a different fixture.
        # temperature/max_tokens default to None at this layer (Plan 4 doesn't
        # expose them per-call); a future plan that adds per-call decoding
        # params updates this call-site without changing the signature.
        fp = request_fingerprint(
            messages,
            tools=tools,
            model=self.model_name(),
            structured_output=structured_output,
        )
        if fp in self._scripts:
            return self._scripts[fp]
        if self._config.strict:
            raise LLMProviderError(
                f"MockLLMProvider: no fixture for fingerprint {fp}. "
                "Add an entry under "
                f"{self._config.scripts_dir}/."
            )
        return ChatResponse(
            content="",
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            finish_reason="stop",
            tool_calls=[],
        )

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def model_name(self) -> str:
        return "mock"

    def max_context_tokens(self) -> int:
        return 1_000_000  # mock doesn't impose a real ceiling
