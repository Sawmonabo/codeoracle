"""
Pure Pydantic domain types. No I/O. No imports from db/, adapters/, services/.

Public re-exports below let callers do `from codeoracle.models import Claim,
Verdict`
without knowing the file layout.
"""

from codeoracle.models.embedding import EmbeddingChunk, EmbeddingHit
from codeoracle.models.evidence import (
    Citation,
    Claim,
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult, QueryStatus
from codeoracle.models.snapshot import (
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
    compute_dirty_tree_sha,
)
from codeoracle.models.trust import SourceKind, TrustLevel

__all__ = [
    "Citation",
    "Claim",
    "DraftCitation",
    "DraftClaim",
    "EmbeddingChunk",
    "EmbeddingHit",
    "EvidenceHandle",
    "ExtractorOutcome",
    "HandleType",
    "Query",
    "QueryResult",
    "QueryStatus",
    "ResolvedCitation",
    "Snapshot",
    "SnapshotStatus",
    "SourceKind",
    "TrustLevel",
    "Verdict",
    "VerdictStatus",
    "compute_dirty_tree_sha",
]
