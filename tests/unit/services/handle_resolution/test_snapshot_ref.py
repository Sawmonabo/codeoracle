"""Plan 8 Task 8a — head_sha_only snapshot-ref helper tests.

Pins:
  1. The CodeOracle dirty-tree form `<head>+wt<12hex>` is stripped to `<head>`.
  2. A clean SHA (no suffix) passes through unchanged.
  3. A non-canonical suffix length (e.g. `+wt0123`) is NOT recognized and
     passes through unchanged — strict-match guards against accidental
     stripping of unrelated +wt-like substrings.
  4. The match is lowercase-only per the CodeOracle format; an uppercase
     `+WT...` does NOT match and passes through unchanged.
  5. Empty string passes through unchanged (no crash).
"""

from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only


def test_head_sha_only_strips_dirty_tree_wt_suffix() -> None:
    assert head_sha_only("abc123+wt0123456789ab") == "abc123"


def test_head_sha_only_passes_clean_sha_unchanged() -> None:
    assert head_sha_only("abc123") == "abc123"


def test_head_sha_only_passes_short_wt_suffix_unchanged() -> None:
    """CodeOracle format is `+wt` + exactly 12 hex chars; anything else is
    not the CodeOracle format and must pass through verbatim."""
    assert head_sha_only("abc123+wt0123") == "abc123+wt0123"


def test_head_sha_only_passes_uppercase_wt_unchanged() -> None:
    """Strict-lowercase match — CodeOracle writes `+wt`, not `+WT`."""
    assert head_sha_only("abc123+WT0123456789ab") == "abc123+WT0123456789ab"


def test_head_sha_only_passes_empty_string_unchanged() -> None:
    assert head_sha_only("") == ""
