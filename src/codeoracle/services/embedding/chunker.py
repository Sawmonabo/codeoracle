"""Chunker family — split a file's lines into embeddable chunks.

Plan 6 ships two chunker implementations behind one ABC, dispatched per file
by `chunker_for(file_path)`:

- LineChunker (code files: .py, .pyi) — fixed-size overlapping line windows.
- ParagraphChunker (doc files: .md, .rst, .adoc, .markdown) — semantic
  paragraph runs bounded by blank lines.

Storage shape `(start_line, end_line, text)` is uniform across implementations;
only the chunking algorithm differs. Plan 8's CodeSpanResolver and
DocParagraphResolver both read `(chunk_start_line, chunk_end_line)` directly
from each `embeddings` row — they MUST NOT assume embed_chunk_lines-sized
spans, because doc chunks are variable-length. See Architectural Invariant 5.

Both writer (workers/embed_indexer) and reader (Plan 8 resolvers) load the
same `EmbeddingConfig` instance via `get_settings().embedding`, so any
env-override (CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES,
CODEORACLE_EMBEDDING__DOC_CHUNK_MAX_LINES) flows to both sides in lockstep.
"""

import pathlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Settings import lives in TYPE_CHECKING to avoid a runtime cycle:
    # core/config.py never imports this module, so a deferred type-only
    # hint here is the cleanest way to type-annotate `chunker_for`.
    from codeoracle.core.config import EmbeddingConfig


@dataclass(frozen=True, slots=True)
class Chunk:
    """
    One chunk's metadata + text. Line numbers are 1-indexed and inclusive.
    """

    start_line: int
    end_line: int
    text: str


class Chunker(ABC):
    """
    Strategy ABC. Implementations differ in how they slice lines into chunks;
    storage shape `(start_line, end_line, text)` is uniform.

    Implementations MUST be pure (no I/O), deterministic, and return chunks in
    file order with strictly non-decreasing `start_line` (so the writer can
    insert them with predictable ordering).
    """

    @abstractmethod
    def chunk(self, lines: list[str]) -> list[Chunk]: ...


class LineChunker(Chunker):
    """Fixed-size overlapping line windows. Used for code files.

    Algorithm: stride = chunk_lines - overlap. First chunk covers [1,
    chunk_lines];
    each subsequent chunk starts `stride` lines later. The last chunk's
    end_line is clamped to the file's total line count.
    """

    def __init__(self, *, chunk_lines: int, overlap: int) -> None:
        if chunk_lines <= 0:
            raise ValueError(
                f"chunk_lines must be positive, got {chunk_lines}"
            )
        if overlap < 0:
            raise ValueError(f"overlap must be non-negative, got {overlap}")
        if overlap >= chunk_lines:
            raise ValueError(
                f"overlap ({overlap}) must be strictly less than "
                f"chunk_lines ({chunk_lines})"
            )
        # NOTE: the stride floor that rejects degenerate geometries like
        # chunk_lines=2, overlap=1 (stride=1 → O(file) near-duplicate chunks)
        # lives in EmbeddingConfig's `_validate_embedding_config`
        # (core/config.py,
        # Task 3, D-6.13) — the single owned gate. `chunker_for` is the only
        # production construction site and always feeds already-validated
        # config
        # values, so no second floor is re-asserted here (one authoritative
        # invariant at the config boundary, not two kept in lockstep).
        self._chunk_lines = chunk_lines
        self._overlap = overlap

    def chunk(self, lines: list[str]) -> list[Chunk]:
        if not lines:
            return []
        total = len(lines)
        stride = self._chunk_lines - self._overlap
        chunks: list[Chunk] = []
        start_zero = 0  # 0-indexed for math
        while start_zero < total:
            end_zero = min(start_zero + self._chunk_lines, total)
            text = "\n".join(lines[start_zero:end_zero])
            chunks.append(
                Chunk(
                    start_line=start_zero + 1,  # 1-indexed
                    end_line=end_zero,  # 1-indexed (inclusive)
                    text=text,
                )
            )
            if end_zero == total:
                break
            start_zero += stride
        return chunks


class ParagraphChunker(Chunker):
    """Markdown-style paragraph chunker. Used for doc files (.md, .rst, .adoc).

    A paragraph is a run of consecutive non-blank lines (CommonMark §4.8).
    Blank lines (empty or whitespace-only) act as paragraph separators and
    are NOT included in any chunk. Each paragraph becomes one Chunk.

    Paragraphs longer than `max_paragraph_lines` are split via
    LineChunker-style
    stride (no overlap — paragraphs are already semantic units). This guards
    against pathological inputs (one giant code-fence dump, a generated
    table-of-symbols) that would otherwise produce one enormous embedding
    request and blow the context window.

    Empty input or all-blank input returns []. A file with no blank lines
    returns one chunk covering the whole file (or N chunks if it exceeds
    `max_paragraph_lines`).
    """

    def __init__(self, *, max_paragraph_lines: int) -> None:
        if max_paragraph_lines <= 0:
            raise ValueError(
                "max_paragraph_lines must be positive, got "
                f"{max_paragraph_lines}"
            )
        self._max = max_paragraph_lines

    @staticmethod
    def _is_blank(line: str) -> bool:
        return not line.strip()

    def chunk(self, lines: list[str]) -> list[Chunk]:
        if not lines:
            return []
        chunks: list[Chunk] = []
        i = 0
        total = len(lines)
        while i < total:
            # Skip blank lines.
            if self._is_blank(lines[i]):
                i += 1
                continue
            # Collect a run of non-blank lines.
            run_start = i  # 0-indexed
            while i < total and not self._is_blank(lines[i]):
                i += 1
            run_end = i  # 0-indexed exclusive

            # Emit one chunk per `max_paragraph_lines` slice of this paragraph.
            slice_start = run_start
            while slice_start < run_end:
                slice_end = min(slice_start + self._max, run_end)
                chunks.append(
                    Chunk(
                        start_line=slice_start + 1,  # 1-indexed
                        end_line=slice_end,  # 1-indexed (inclusive)
                        text="\n".join(lines[slice_start:slice_end]),
                    )
                )
                slice_start = slice_end
        return chunks


# File extensions routed to ParagraphChunker. All others (including unknown)
# get LineChunker. Lowercase comparison; suffix taken via pathlib.PurePosixPath
# so the same logic works on Windows-style path strings passed through the
# orchestrator unchanged.
_DOC_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".rst", ".adoc", ".markdown"}
)


def is_doc_path(source_path: str) -> bool:
    """
    Whether `source_path` is a documentation file (Markdown / reST / AsciiDoc).

    Public path-classification predicate, parallel to `chunker_for`'s internal
    dispatch. Plan 10's `embed_query` node uses this to route an `EmbeddingHit`
    to `HandleType.DOC_PARAGRAPH` (doc extension) vs `HandleType.CODE_SPAN`
    (everything else), reusing the same `_DOC_EXTENSIONS` constant
    `chunker_for`
    consults — so what got chunked as a doc cites as a doc, no drift.

    Suffix comparison matches `chunker_for`: lowercase, `pathlib.PurePosixPath`
    so Windows-style separators in path strings are handled unchanged.
    """
    return pathlib.PurePosixPath(source_path).suffix.lower() in _DOC_EXTENSIONS


def chunker_for(
    file_path: str, *, embedding_config: EmbeddingConfig
) -> Chunker:
    """Dispatch by file extension. Used by the embed-indexer consumer to pick
    the right chunker per message — code files get LineChunker, doc files get
    ParagraphChunker, unknown extensions default to LineChunker (conservative:
    a `.txt` file still gets a meaningful overlapping-line treatment).

    The embed extractor's orchestrator-side filter is
    `accepted_extensions=None`
    (Task 2 `_INFO_BY_KIND["embed"]`), so this factory sees every kind of file.
    Code extractors (joern/scip/opengrep) never see doc files at all — that
    filtering happens at fan-out (`IndexingOrchestrator._filter_files_for`).
    """
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    if suffix in _DOC_EXTENSIONS:
        return ParagraphChunker(
            max_paragraph_lines=embedding_config.doc_chunk_max_lines
        )
    return LineChunker(
        chunk_lines=embedding_config.embed_chunk_lines,
        overlap=embedding_config.embed_chunk_overlap,
    )
