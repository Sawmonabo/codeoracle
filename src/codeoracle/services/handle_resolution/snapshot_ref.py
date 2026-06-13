"""Snapshot-ref normalization for handle resolution.

CodeOracle's snapshot SHA encoding includes a ``+wt<12hex>`` suffix when
the working tree is dirty at extraction time (see core/snapshot.py). Sourcebot
indexes only true git refs, so resolvers must strip the suffix before invoking
the adapter. The underlying head SHA is what Sourcebot has indexed; dirty-tree
deltas are unresolvable from Sourcebot's view.

Free-function exception to the OOP preference: pure string transformation with
no state and no I/O. Documented in CLAUDE.md and project memory.

Closes audit finding G21 / M-8.1 (dirty-tree SHA produces 404 against
Sourcebot).
"""

import re

_WT_SUFFIX_RE = re.compile(r"^([0-9a-f]+)\+wt[0-9a-f]{12}$")


def head_sha_only(snapshot_sha: str) -> str:
    """Strip the ``+wt<12hex>`` suffix from a CodeOracle snapshot SHA.

    Returns the underlying head SHA when the suffix matches exactly; otherwise
    returns the input unchanged. Match is strict-lowercase per CodeOracle
    format.

    Args: snapshot_sha: A CodeOracle snapshot SHA. May or may not carry the
        dirty-tree suffix.

    Returns: The head SHA suitable for use as a Sourcebot git ref.
    """
    match = _WT_SUFFIX_RE.match(snapshot_sha)
    return match.group(1) if match else snapshot_sha
