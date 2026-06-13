"""Plan 4 Task 11: OllamaProvider unit tests (respx-mocked)."""

import httpx
import pytest
import respx

from codeoracle.adapters.llm.config import OllamaProviderConfig
from codeoracle.adapters.llm.providers import ollama as ollama_module
from codeoracle.adapters.llm.providers.ollama import OllamaProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage


@pytest.fixture(autouse=True)
def _no_hub_fetch(monkeypatch):
    """Provider __init__ calls load_hf_tokenizer(model); an Ollama tag is not a
    Hub id, so this would attempt (and fail) a Hub fetch. Patch it to None so
    construction is network-free; count_tokens then uses the len//4 fallback.
    """
    monkeypatch.setattr(ollama_module, "load_hf_tokenizer", lambda model: None)


def _provider() -> OllamaProvider:
    return OllamaProvider(
        OllamaProviderConfig(
            base_url="http://ollama-test:11434", model="qwen2.5:32b"
        ),
    )


async def test_ollama_chat_happy_path():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://ollama-test:11434/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "qwen2.5:32b",
                    "message": {"role": "assistant", "content": "pong"},
                    "done": True,
                    "prompt_eval_count": 4,
                    "eval_count": 1,
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="ping")]
        )
        assert response.content == "pong"
        assert response.usage.input_tokens == 4
        assert response.usage.output_tokens == 1
        assert response.finish_reason == "stop"


async def test_ollama_wraps_http_error():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://ollama-test:11434/api/chat").mock(
            return_value=httpx.Response(
                500, json={"error": "model not loaded"}
            ),
        )
        provider = _provider()
        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat([ChatMessage(role="user", content="ping")])
        assert "Ollama HTTP error" in str(exc_info.value)


async def test_ollama_not_done_maps_to_length():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://ollama-test:11434/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "qwen2.5:32b",
                    "message": {"role": "assistant", "content": "partial"},
                    "done": False,
                    "prompt_eval_count": 4,
                    "eval_count": 100,
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="generate at length")]
        )
        assert response.finish_reason == "length"


async def test_ollama_count_tokens_falls_back_to_heuristic_for_ollama_tag():
    """
    An Ollama tag isn't a Hub id (autouse fixture forces the loader to None),
    so count_tokens uses the labeled len//4 fallback — empty → 0, else len//4.
    """
    provider = _provider()
    assert provider.count_tokens("") == 0
    assert provider.count_tokens("abcdefgh") == 2  # 8 // 4
