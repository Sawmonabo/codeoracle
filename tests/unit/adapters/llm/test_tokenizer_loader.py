"""Plan 4 Task 9b: tokenizer_loader unit tests. No network — the real-load path
is patched; only the fallback formula and the None-on-failure contract are
asserted here (a live load is exercised by the providers' integration path)."""

from codeoracle.adapters.llm import tokenizer_loader
from codeoracle.adapters.llm.tokenizer_loader import (
    count_tokens_with_fallback,
    load_hf_tokenizer,
)


class _StubEncoding:
    def __init__(self, n: int) -> None:
        self.ids = list(range(n))


class _StubTokenizer:
    def __init__(self, n: int) -> None:
        self._n = n

    def encode(self, text: str) -> _StubEncoding:
        return _StubEncoding(self._n)


def test_fallback_empty_is_zero():
    assert count_tokens_with_fallback(None, "") == 0
    assert count_tokens_with_fallback(_StubTokenizer(5), "") == 0


def test_fallback_heuristic_when_tokenizer_none():
    assert count_tokens_with_fallback(None, "abcdefgh") == 2  # 8 // 4
    assert count_tokens_with_fallback(None, "a") == 1  # max(1, 0)


def test_exact_count_when_tokenizer_present():
    assert count_tokens_with_fallback(_StubTokenizer(7), "anything") == 7


def test_load_returns_none_and_logs_on_failure(monkeypatch):
    """A non-repo-id string (Ollama tag) must yield None, not raise."""

    def _boom(model_id: str):
        raise ValueError(f"not a repo id: {model_id}")

    monkeypatch.setattr(
        tokenizer_loader.Tokenizer, "from_pretrained", staticmethod(_boom)
    )
    assert load_hf_tokenizer("qwen2.5:32b") is None


def test_load_returns_tokenizer_on_success(monkeypatch):
    sentinel = _StubTokenizer(3)
    monkeypatch.setattr(
        tokenizer_loader.Tokenizer,
        "from_pretrained",
        staticmethod(lambda model_id: sentinel),
    )
    assert load_hf_tokenizer("Qwen/Qwen3.6-32B") is sentinel
