"""Shared Hugging Face tokenizer loading + heuristic fallback for the
local-model providers (vLLM, Ollama).

`load_hf_tokenizer(model_id)` fetches the model's `tokenizer.json` from the
Hub ONCE (then disk-cached by huggingface_hub) and returns a ready
`tokenizers.Tokenizer`, or None if it can't load (offline, gated/private repo,
or `model_id` isn't a Hub repo id — e.g. an Ollama tag like "qwen2.5:32b").
The WARNING is logged HERE, once per failed load, so each provider's
count_tokens stays a one-liner and the operator sees exactly one signal that
the budget gate is running on an approximation.

`count_tokens_with_fallback(tokenizer, text)` is the single definition of the
labeled `len // 4` fallback — used when `tokenizer is None`. Centralizing it
means the fallback formula can never drift between vLLM and Ollama.
"""

from collections.abc import Sequence
from typing import Protocol

from tokenizers import Tokenizer

from codeoracle.core.logging import get_logger

_logger = get_logger(__name__)


class TokenEncoding(Protocol):
    @property
    def ids(self) -> Sequence[int]: ...


class TokenizerLike(Protocol):
    def encode(self, text: str) -> TokenEncoding: ...


def load_hf_tokenizer(model_id: str) -> Tokenizer | None:
    """Return the model's HF tokenizer, or None (logged WARNING) if it can't
    load. Called once per provider construction; the encode hot-path that uses
    the returned tokenizer makes zero network round-trips."""
    try:
        return Tokenizer.from_pretrained(model_id)
    except Exception as exc:
        # Broad catch is deliberate: from_pretrained raises a range of
        # exceptions (network, 401/404 from the Hub, malformed repo, value
        # errors on non-repo-id strings). ALL of them mean "no exact tokenizer
        # available" → fall back. Logged so the degradation is never silent.
        _logger.warning(
            "hf_tokenizer_load_failed_using_heuristic",
            model=model_id,
            error=str(exc),
            fallback="len//4",
        )
        return None


def count_tokens_with_fallback(
    tokenizer: Tokenizer | TokenizerLike | None,
    text: str,
) -> int:
    """Exact length via `tokenizer.encode(text)` when a tokenizer is present;
    otherwise the labeled `len(text) // 4` last-resort heuristic. Empty text is
    always 0. The single source of truth for the fallback formula."""
    if not text:
        return 0
    if tokenizer is not None:
        return len(tokenizer.encode(text).ids)
    return max(1, len(text) // 4)
