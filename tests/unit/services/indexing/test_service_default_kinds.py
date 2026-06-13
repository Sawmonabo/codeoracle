"""Plan 6 changes IndexingService's default extractor_kinds.

This pins the public contract: a caller constructing IndexingService with no
explicit kinds gets all four extractors fanned out. Plan 7 may add a 5th
("traces") if traces are added to the snapshot-keyed barrier; the current
expectation is that traces are CONTINUOUS and do NOT join this set.
"""

from codeoracle.services.indexing.service import (
    DEFAULT_EXTRACTOR_KINDS,
)


def test_default_includes_all_four() -> None:
    assert set(DEFAULT_EXTRACTOR_KINDS) == {
        "joern",
        "scip",
        "opengrep",
        "embed",
    }


def test_default_does_not_include_traces() -> None:
    """Plan 7 ContinuousConsumer + traces_ingest is out of scope for the
    snapshot-keyed barrier. Asserting absence pins the design."""
    assert "traces" not in DEFAULT_EXTRACTOR_KINDS
    assert "sourcebot" not in DEFAULT_EXTRACTOR_KINDS
