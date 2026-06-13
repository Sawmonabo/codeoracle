"""
chunker_for + is_doc_path — extension-based dispatch + the public path
predicate.

`chunker_for` is the embed-indexer's internal factory; `is_doc_path` is the
public predicate Plan 10's `embed_query` uses to route knn hits to
DOC_PARAGRAPH vs CODE_SPAN. Both consult the same `_DOC_EXTENSIONS` set, so
they MUST agree per-path — the cross-`is_doc_path`/`chunker_for` consistency
test below pins that contract.
"""

import pytest

from codeoracle.core.config import EmbeddingConfig
from codeoracle.services.embedding.chunker import (
    LineChunker,
    ParagraphChunker,
    chunker_for,
    is_doc_path,
)


def _config() -> EmbeddingConfig:
    return EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )


def test_python_file_dispatches_to_line_chunker() -> None:
    assert isinstance(
        chunker_for("foo.py", embedding_config=_config()), LineChunker
    )


def test_python_stub_file_dispatches_to_line_chunker() -> None:
    assert isinstance(
        chunker_for("types.pyi", embedding_config=_config()), LineChunker
    )


def test_markdown_file_dispatches_to_paragraph_chunker() -> None:
    assert isinstance(
        chunker_for("README.md", embedding_config=_config()), ParagraphChunker
    )


def test_uppercase_markdown_extension_dispatches_to_paragraph_chunker() -> (
    None
):
    """Suffix comparison is case-insensitive (real repos have README.MD)."""
    assert isinstance(
        chunker_for("CHANGELOG.MD", embedding_config=_config()),
        ParagraphChunker,
    )


def test_rst_file_dispatches_to_paragraph_chunker() -> None:
    assert isinstance(
        chunker_for("docs/intro.rst", embedding_config=_config()),
        ParagraphChunker,
    )


def test_asciidoc_file_dispatches_to_paragraph_chunker() -> None:
    assert isinstance(
        chunker_for("api.adoc", embedding_config=_config()), ParagraphChunker
    )


def test_long_markdown_extension_dispatches_to_paragraph_chunker() -> None:
    """Some repos use the long form .markdown."""
    assert isinstance(
        chunker_for("README.markdown", embedding_config=_config()),
        ParagraphChunker,
    )


def test_unknown_extension_dispatches_to_line_chunker() -> None:
    """Conservative default: anything not in _DOC_EXTENSIONS gets LineChunker.
    A .txt file still produces meaningful overlapping-line chunks.
    """
    assert isinstance(
        chunker_for("notes.txt", embedding_config=_config()), LineChunker
    )


def test_no_extension_dispatches_to_line_chunker() -> None:
    """
    Files like `LICENSE` or `Dockerfile` with no suffix → LineChunker default.
    """
    assert isinstance(
        chunker_for("LICENSE", embedding_config=_config()), LineChunker
    )


def test_line_chunker_inherits_config_geometry() -> None:
    """
    LineChunker built by factory carries embed_chunk_lines +
    embed_chunk_overlap.
    """
    config = _config()
    chunker = chunker_for("foo.py", embedding_config=config)
    # Exercise via behavior, not private attribute access.
    lines = [f"l{i}" for i in range(1, 121)]
    chunks = chunker.chunk(lines)
    # With defaults (100 lines, 20 overlap) on 120 lines → 2 chunks.
    assert len(chunks) == 2
    assert chunks[0].end_line == 100
    assert chunks[1].start_line == 81


def test_paragraph_chunker_inherits_config_geometry() -> None:
    """ParagraphChunker built by factory carries doc_chunk_max_lines."""
    config = EmbeddingConfig(
        model="m", dim=1536, kind="mock", doc_chunk_max_lines=5
    )
    chunker = chunker_for("README.md", embedding_config=config)
    # 12 non-blank lines, max=5 → 3 chunks (5, 5, 2).
    lines = [f"line {i}" for i in range(1, 13)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert chunks[0].end_line == 5
    assert chunks[1].start_line == 6
    assert chunks[1].end_line == 10
    assert chunks[2].start_line == 11
    assert chunks[2].end_line == 12


# --- is_doc_path predicate (Plan 10 cross-plan consumer) ---------------------


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/intro.rst",
        "api.adoc",
        "README.markdown",
        "CHANGELOG.MD",  # case-insensitive (mirrors chunker_for)
        "subdir/a/b/notes.md",
        # Windows-style separators handled by PurePosixPath
        "C:\\repo\\README.md",
    ],
)
def test_is_doc_path_recognises_doc_extensions(path: str) -> None:
    assert is_doc_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "foo.py",
        "types.pyi",
        # unknown extension is NOT a doc (matches LineChunker default)
        "notes.txt",
        "LICENSE",  # no extension is NOT a doc
        "Dockerfile",
        "foo.json",
        "foo.markdown.bak",  # the LAST suffix wins; .bak is not a doc
    ],
)
def test_is_doc_path_rejects_non_doc_extensions(path: str) -> None:
    assert is_doc_path(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "foo.py",
        "types.pyi",
        "README.md",
        "docs/intro.rst",
        "api.adoc",
        "README.markdown",
        "CHANGELOG.MD",
        "notes.txt",
        "LICENSE",
    ],
)
def test_is_doc_path_agrees_with_chunker_for_dispatch(path: str) -> None:
    """The two paths through the doc-vs-code decision MUST agree per-path.

    If they diverge — e.g., `chunker_for` adds `.txt` to the doc set but
    `is_doc_path` doesn't — Plan 10 would emit CODE_SPAN citations for chunks
    that were paragraph-chunked, mis-classifying the evidence kind. This test
    pins single-source-of-truth: both consult `_DOC_EXTENSIONS`, so they
    cannot disagree without a code-level change to the constant.
    """
    is_doc = is_doc_path(path)
    chunker = chunker_for(path, embedding_config=_config())
    assert isinstance(chunker, ParagraphChunker) is is_doc
