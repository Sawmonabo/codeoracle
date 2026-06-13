"""Embedding domain records shared by workers, repositories, and the agent."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingChunk:
    """One chunk ready to be written to the embeddings table."""

    snapshot_sha: str
    source_path: str
    chunk_start_line: int
    chunk_end_line: int
    content: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class EmbeddingHit:
    """One nearest-neighbor hit returned from the embeddings table."""

    snapshot_sha: str
    source_path: str
    chunk_start_line: int
    chunk_end_line: int
    content: str
    distance: float
