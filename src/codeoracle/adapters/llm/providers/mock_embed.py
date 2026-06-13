"""MockEmbeddingsProvider — deterministic test double.

NOT in `tests/` because non-test callers (e.g., E2E integration tests in
`tests/e2e/` that orchestrate real workers but want stable embeddings)
import it directly.

Deterministic strategy: hash each input string into a seeded RNG, produce
a unit vector. Same input → same vector. Different input → very likely
different vector (collision probability ≈ 2^-64, fine for tests).
"""

import hashlib
import math
import random

from codeoracle.adapters.llm.embedding_base import EmbeddingsProvider


class MockEmbeddingsProvider(EmbeddingsProvider):
    def __init__(self, dim: int) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._stable_unit_vector(t) for t in texts]

    def _stable_unit_vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # First 8 bytes as a u64 seed for the RNG.
        seed = int.from_bytes(digest[:8], byteorder="big", signed=False)
        rng = random.Random(seed)
        raw = [rng.gauss(0.0, 1.0) for _ in range(self._dim)]
        magnitude = math.sqrt(sum(x * x for x in raw))
        if magnitude == 0.0:  # astronomically unlikely; defensive
            raw[0] = 1.0
            return raw
        return [x / magnitude for x in raw]
