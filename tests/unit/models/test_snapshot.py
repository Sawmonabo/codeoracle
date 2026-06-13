"""Snapshot model + dirty-tree SHA tests."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from codeoracle.models.snapshot import (
    KNOWN_EXTRACTORS,
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
    compute_dirty_tree_sha,
)


def test_clean_tree_returns_head_sha_unchanged():
    assert compute_dirty_tree_sha("abc1234", []) == "abc1234"


def test_dirty_tree_returns_prefixed_sha():
    sha = compute_dirty_tree_sha("abc1234", [("file.py", "x = 1\n")])
    assert sha.startswith("abc1234+wt")
    assert (
        len(sha) == len("abc1234") + 3 + 12
    )  # "abc1234" + "+wt" + 12 hex chars


def test_dirty_tree_sha_is_order_independent():
    a = compute_dirty_tree_sha("abc", [("a", "1"), ("b", "2")])
    b = compute_dirty_tree_sha("abc", [("b", "2"), ("a", "1")])
    assert a == b


def test_dirty_tree_sha_changes_when_content_changes():
    a = compute_dirty_tree_sha("abc", [("a", "1")])
    b = compute_dirty_tree_sha("abc", [("a", "2")])
    assert a != b


def test_dirty_tree_sha_distinguishes_path_boundaries():
    """The b'\\0' delimiter ensures ('ab', 'cd') != ('a', 'bcd')."""
    a = compute_dirty_tree_sha("abc", [("ab", "cd")])
    b = compute_dirty_tree_sha("abc", [("a", "bcd")])
    assert a != b


def test_snapshot_defaults():
    s = Snapshot(sha="abc1234")
    assert s.status == SnapshotStatus.PENDING
    assert s.parent_sha is None
    assert s.changed_files == []
    assert s.extractor_results == {}


def test_snapshot_sha_min_length():
    with pytest.raises(ValidationError):
        Snapshot(sha="abc")  # 3 chars — below min 4


def test_snapshot_extractor_results_with_known_keys():
    s = Snapshot(
        sha="abc1234",
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
    )
    assert s.extractor_results["joern"] == ExtractorOutcome.SUCCESS


def test_known_extractors_is_snapshot_extractor_set():
    """
    KNOWN_EXTRACTORS is the closed set of keys valid in
    Snapshot.extractor_results.

    These are the four snapshot-keyed extractors fanned out by Plan 6's
    IndexingService (DEFAULT_EXTRACTOR_KINDS). traces_ingest (Plan 7) is a
    ContinuousConsumer that writes to traces.spans, not extractor_results;
    Sourcebot is git-pull (no indexer worker — Plan 5 §"Defers to Plan 6").
    """
    assert {"joern", "scip", "opengrep", "embed"} == KNOWN_EXTRACTORS


def test_snapshot_status_transitions_are_strings():
    """Defensive: status is a str enum so JSON serialization is stable."""
    assert SnapshotStatus.APPLIED.value == "applied"
    assert SnapshotStatus.FAILED.value == "failed"


def test_snapshot_applied_at_can_be_set():
    when = datetime(2026, 5, 21, 12, 0, 0)
    s = Snapshot(sha="abc1234", status=SnapshotStatus.APPLIED, applied_at=when)
    assert s.applied_at == when
