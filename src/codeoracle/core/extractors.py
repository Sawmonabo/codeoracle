"""Extractor routing metadata."""

from dataclasses import dataclass
from typing import Literal

from codeoracle.core.streams import (
    STREAM_INDEXING_EMBED,
    STREAM_INDEXING_JOERN,
    STREAM_INDEXING_OPENGREP,
    STREAM_INDEXING_SCIP,
)

ExtractorMode = Literal["per_file", "project"]

DEFAULT_EXTRACTOR_KINDS: tuple[str, ...] = (
    "joern",
    "scip",
    "opengrep",
    "embed",
)


@dataclass(frozen=True, slots=True)
class ExtractorInfo:
    stream: str
    mode: ExtractorMode
    accepted_extensions: tuple[str, ...] | None


INFO_BY_KIND: dict[str, ExtractorInfo] = {
    "joern": ExtractorInfo(
        stream=STREAM_INDEXING_JOERN,
        mode="per_file",
        accepted_extensions=(".py", ".pyi"),
    ),
    "scip": ExtractorInfo(
        stream=STREAM_INDEXING_SCIP,
        mode="project",
        accepted_extensions=(".py", ".pyi"),
    ),
    "opengrep": ExtractorInfo(
        stream=STREAM_INDEXING_OPENGREP,
        mode="per_file",
        accepted_extensions=(".py", ".pyi"),
    ),
    "embed": ExtractorInfo(
        stream=STREAM_INDEXING_EMBED,
        mode="per_file",
        accepted_extensions=None,
    ),
}
