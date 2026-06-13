"""ParagraphChunker — pure-function tests for doc-file chunking (G14).

A paragraph is a run of consecutive non-blank lines bounded by blank lines
(CommonMark §4.8). Each paragraph becomes one Chunk. Paragraphs longer than
max_paragraph_lines split via stride.
"""

import pytest

from codeoracle.services.embedding.chunker import Chunk, ParagraphChunker


def test_empty_input_returns_empty_list() -> None:
    chunker = ParagraphChunker(max_paragraph_lines=200)
    assert chunker.chunk([]) == []


def test_only_blank_lines_returns_empty_list() -> None:
    """A doc file with only blank lines has no paragraphs → no chunks."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    assert chunker.chunk(["", "   ", "\t", ""]) == []


def test_single_paragraph_one_chunk() -> None:
    """
    A run of non-blank lines with no internal blanks → one chunk covering it.
    """
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(
        ["# Heading", "Paragraph line 1.", "Paragraph line 2."]
    )
    assert chunks == [
        Chunk(
            start_line=1,
            end_line=3,
            text="# Heading\nParagraph line 1.\nParagraph line 2.",
        )
    ]


def test_two_paragraphs_separated_by_blank_line() -> None:
    """Two non-blank runs separated by a blank → two chunks. Blank line is
    NOT included in either chunk."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(
        ["Para A line 1", "Para A line 2", "", "Para B line 1"]
    )
    assert chunks == [
        Chunk(start_line=1, end_line=2, text="Para A line 1\nPara A line 2"),
        Chunk(start_line=4, end_line=4, text="Para B line 1"),
    ]


def test_multiple_blank_lines_between_paragraphs() -> None:
    """Several blank lines in a row still separate exactly two paragraphs;
    chunk indices skip across all the blanks."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["A", "", "", "", "B"])
    assert chunks == [
        Chunk(start_line=1, end_line=1, text="A"),
        Chunk(start_line=5, end_line=5, text="B"),
    ]


def test_leading_blank_lines_are_skipped() -> None:
    """Doc files often start with blank lines or front-matter blanks;
    chunker advances past them."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["", "", "Heading"])
    assert chunks == [Chunk(start_line=3, end_line=3, text="Heading")]


def test_trailing_blank_lines_are_skipped() -> None:
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["Heading", "", ""])
    assert chunks == [Chunk(start_line=1, end_line=1, text="Heading")]


def test_whitespace_only_line_treated_as_blank() -> None:
    """A line containing only spaces or tabs separates paragraphs."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["A", "   \t  ", "B"])
    assert chunks == [
        Chunk(start_line=1, end_line=1, text="A"),
        Chunk(start_line=3, end_line=3, text="B"),
    ]


def test_long_paragraph_splits_into_chunks_of_max_paragraph_lines() -> None:
    """A 7-line paragraph with max=3 splits into chunks (3, 3, 1)."""
    chunker = ParagraphChunker(max_paragraph_lines=3)
    lines = [f"line {i}" for i in range(1, 8)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert chunks[0] == Chunk(
        start_line=1, end_line=3, text="line 1\nline 2\nline 3"
    )
    assert chunks[1] == Chunk(
        start_line=4, end_line=6, text="line 4\nline 5\nline 6"
    )
    assert chunks[2] == Chunk(start_line=7, end_line=7, text="line 7")


def test_max_paragraph_lines_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_paragraph_lines"):
        ParagraphChunker(max_paragraph_lines=0)
    with pytest.raises(ValueError, match="max_paragraph_lines"):
        ParagraphChunker(max_paragraph_lines=-5)


def test_realistic_markdown_block() -> None:
    """Real-world shape: headings, paragraphs, a code-fence (treated as one
    paragraph because it has no internal blank lines), and a list."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    lines = [
        "# Title",
        "",
        "Introductory paragraph",
        "with two lines.",
        "",
        "```python",
        "def foo():",
        "    return 42",
        "```",
        "",
        "- bullet one",
        "- bullet two",
    ]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 4
    # Heading
    assert chunks[0] == Chunk(start_line=1, end_line=1, text="# Title")
    # Intro paragraph
    assert chunks[1] == Chunk(
        start_line=3,
        end_line=4,
        text="Introductory paragraph\nwith two lines.",
    )
    # Code fence (no blanks inside → one chunk)
    assert chunks[2].start_line == 6
    assert chunks[2].end_line == 9
    assert "def foo()" in chunks[2].text
    # Bullet list (consecutive non-blanks → one chunk)
    assert chunks[3] == Chunk(
        start_line=11, end_line=12, text="- bullet one\n- bullet two"
    )
