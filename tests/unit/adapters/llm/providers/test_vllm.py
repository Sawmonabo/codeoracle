"""Plan 4 Task 10: VLLMProvider unit tests (respx-mocked)."""

from typing import ClassVar

import httpx
import pytest
import respx

from codeoracle.adapters.llm.config import VLLMProviderConfig
from codeoracle.adapters.llm.providers import vllm as vllm_module
from codeoracle.adapters.llm.providers.vllm import VLLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage


@pytest.fixture(autouse=True)
def _no_hub_fetch(monkeypatch):
    """Provider __init__ now calls load_hf_tokenizer(model), which would fetch
    tokenizer.json from the Hub. Patch it to None by default so construction is
    network-free; count_tokens then uses the labeled len//4 fallback. The
    dedicated exact-path test below overrides this with a stub tokenizer."""
    monkeypatch.setattr(vllm_module, "load_hf_tokenizer", lambda model: None)


def _provider() -> VLLMProvider:
    return VLLMProvider(
        VLLMProviderConfig(
            base_url="http://vllm-test:8000/v1", model="Qwen/Qwen3.6-32B"
        ),
    )


async def test_vllm_chat_happy_path():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://vllm-test:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "cmpl-x",
                    "object": "chat.completion",
                    "model": "Qwen/Qwen3.6-32B",
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
                        "prompt_tokens": 4,
                        "completion_tokens": 1,
                        "total_tokens": 5,
                    },
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="ping")]
        )
        assert response.content == "pong"
        assert response.usage.input_tokens == 4


async def test_vllm_wraps_http_error():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://vllm-test:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                503, json={"error": "vllm overloaded"}
            ),
        )
        provider = _provider()
        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat([ChatMessage(role="user", content="ping")])
        assert "vLLM HTTP error" in str(exc_info.value)


async def test_vllm_count_tokens_falls_back_without_tokenizer():
    """When the HF tokenizer didn't load (the autouse fixture forces None),
    count_tokens uses the labeled len//4 fallback — empty → 0, else len//4."""
    provider = _provider()
    assert provider.count_tokens("") == 0
    assert provider.count_tokens("abcdefgh") == 2  # 8 // 4


async def test_vllm_count_tokens_uses_exact_tokenizer_when_present(
    monkeypatch,
):
    """When a tokenizer IS loaded, count_tokens returns its exact encode length
    (here a stub whose Encoding.ids length is deterministic), not the
    heuristic.
    """

    class _StubEncoding:
        ids: ClassVar[list[int]] = [0, 1, 2, 3, 4]  # 5 tokens

    class _StubTokenizer:
        def encode(self, text: str):  # mirrors tokenizers.Tokenizer.encode
            return _StubEncoding()

    monkeypatch.setattr(
        vllm_module, "load_hf_tokenizer", lambda model: _StubTokenizer()
    )
    provider = _provider()
    assert provider.count_tokens("anything") == 5
