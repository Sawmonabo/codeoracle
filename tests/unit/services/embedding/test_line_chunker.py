"""LineChunker — pure-function tests, no I/O."""

import pytest

from codeoracle.services.embedding.chunker import Chunk, LineChunker


def test_empty_input_returns_empty_list() -> None:
    chunker = LineChunker(chunk_lines=100, overlap=20)
    assert chunker.chunk([]) == []


def test_single_line_file() -> None:
    chunker = LineChunker(chunk_lines=100, overlap=20)
    chunks = chunker.chunk(["only line"])
    assert chunks == [Chunk(start_line=1, end_line=1, text="only line")]


def test_short_file_under_chunk_size_single_chunk() -> None:
    """50-line file with chunk_lines=100 → one chunk covering lines 1-50."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 51)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 50
    assert chunks[0].text == "\n".join(lines)


def test_exact_chunk_size_single_chunk() -> None:
    """Exactly chunk_lines lines → one chunk."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 101)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 100


def test_two_chunks_with_overlap() -> None:
    """
    120-line file with chunk_lines=100, overlap=20 → 2 chunks (1-100, 81-120).
    """
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 121)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 2
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 100
    assert chunks[1].start_line == 81
    assert chunks[1].end_line == 120


def test_500_line_file_multiple_chunks_with_overlap() -> None:
    """500-line file with chunk_lines=100, overlap=20 → 6 chunks."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 501)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 6
    expected = [
        (1, 100),
        (81, 180),
        (161, 260),
        (241, 340),
        (321, 420),
        (401, 500),
    ]
    actual = [(c.start_line, c.end_line) for c in chunks]
    assert actual == expected


def test_overlap_zero_means_no_shared_content() -> None:
    chunker = LineChunker(chunk_lines=10, overlap=0)
    lines = [f"line {i}" for i in range(1, 31)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert [(c.start_line, c.end_line) for c in chunks] == [
        (1, 10),
        (11, 20),
        (21, 30),
    ]


def test_overlap_must_be_less_than_chunk_lines() -> None:
    """The chunker rejects invalid geometry to fail fast (mirrors the
    EmbeddingConfig validator). `chunk_lines=100, overlap=100` would loop
    forever.
    """
    with pytest.raises(ValueError, match="overlap"):
        LineChunker(chunk_lines=100, overlap=100)
    with pytest.raises(ValueError, match="overlap"):
        LineChunker(chunk_lines=100, overlap=200)


def test_last_chunk_clamps_to_end_of_file() -> None:
    """A 250-line file with chunk_lines=100, overlap=20 → chunks (1-100),
    (81-180), (161-250) — the last chunk is shorter than chunk_lines."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 251)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert chunks[-1].start_line == 161
    assert chunks[-1].end_line == 250
