"""Plan 6 frame-extension tests for `core/streams.py`.

These tests pin the names of the three new stream constants and three new group
constants. They do not assert exact string values (those are an implementation
detail of `streams.py` and may evolve), but they DO assert that:

(1) Each new constant exists with the expected `STREAM_*` / `GROUP_*` naming.
(2) `dlq_for(STREAM_INDEXING_X)` returns a stable, non-empty string for each
    new stream. This proves DLQ derivation works uniformly for all extractors
    without per-extractor hardcoding.
(3) The three new streams are MUTUALLY DISTINCT and DISTINCT from the existing
    `STREAM_INDEXING_JOERN`. A copy-paste error that aliased two streams would
    silently route SCIP messages to the OpenGrep worker.
"""

from codeoracle.core import streams


def test_stream_constants_plan6_exist_and_are_strings() -> None:
    assert isinstance(streams.STREAM_INDEXING_SCIP, str)
    assert isinstance(streams.STREAM_INDEXING_OPENGREP, str)
    assert isinstance(streams.STREAM_INDEXING_EMBED, str)
    assert streams.STREAM_INDEXING_SCIP != ""
    assert streams.STREAM_INDEXING_OPENGREP != ""
    assert streams.STREAM_INDEXING_EMBED != ""


def test_group_constants_plan6_exist_and_are_strings() -> None:
    assert isinstance(streams.GROUP_SCIP_INDEXER, str)
    assert isinstance(streams.GROUP_OPENGREP_RUNNER, str)
    assert isinstance(streams.GROUP_EMBED_INDEXER, str)


def test_streams_plan6_mutually_distinct() -> None:
    all_streams = {
        streams.STREAM_INDEXING_JOERN,
        streams.STREAM_INDEXING_SCIP,
        streams.STREAM_INDEXING_OPENGREP,
        streams.STREAM_INDEXING_EMBED,
    }
    # Set length == 4 proves no duplicates; a copy-paste alias would collapse
    # two.
    assert len(all_streams) == 4


def test_groups_plan6_mutually_distinct() -> None:
    all_groups = {
        streams.GROUP_JOERN_INDEXER,
        streams.GROUP_SCIP_INDEXER,
        streams.GROUP_OPENGREP_RUNNER,
        streams.GROUP_EMBED_INDEXER,
    }
    assert len(all_groups) == 4


def test_dlq_for_works_for_each_new_stream() -> None:
    # Plan 5 defined `dlq_for(stream) -> str` returning e.g.
    # "indexing.scip.dlq"
    # for an input of "indexing.scip". Plan 6 doesn't re-verify the exact
    # transformation, only that it produces stable distinct outputs for the
    # three new streams.
    dlqs = {
        streams.dlq_for(streams.STREAM_INDEXING_SCIP),
        streams.dlq_for(streams.STREAM_INDEXING_OPENGREP),
        streams.dlq_for(streams.STREAM_INDEXING_EMBED),
    }
    assert len(dlqs) == 3
    for d in dlqs:
        assert isinstance(d, str)
        assert d.endswith(".dlq")
