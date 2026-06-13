"""The evidence + verdict types.

The Draft* / non-Draft split is a load-bearing type-system invariant:
the LLM produces Draft{Citation,Claim} which carry no source_trust.
Plan 9's TrustMatrixService.score_each() converts Draft → final by
attaching platform-computed source_trust. Plan 9's VerifierService.verify()
accepts only Claim — so the LLM cannot forge trust scores.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


class HandleType(StrEnum):
    CODE_SPAN = "code_span"
    GRAPH_QUERY = "graph_query"
    TRACE_SPAN = "trace_span"
    DB_RECORD = "db_record"
    DOC_PARAGRAPH = "doc_paragraph"


class VerdictStatus(StrEnum):
    PROVEN = "PROVEN"
    UNKNOWN = "UNKNOWN"
    REFUTED = "REFUTED"


class EvidenceHandle(BaseModel):
    """
    A pointer to evidence. Carries enough info to fetch the actual content.
    """

    model_config = ConfigDict(frozen=True)

    type: HandleType
    locator: str
    snapshot_sha: Annotated[
        str, StringConstraints(min_length=4, max_length=80)
    ]

    @field_validator("locator")
    @classmethod
    def _scheme_required(cls, v: str) -> str:
        if "://" not in v:
            raise ValueError(
                "EvidenceHandle.locator must include a scheme:// prefix"
            )
        return v


class DraftCitation(BaseModel):
    """LLM-produced citation. No source_trust — that's platform-owned."""

    handle: EvidenceHandle
    excerpt: Annotated[str, StringConstraints(min_length=1)]


class Citation(BaseModel):
    """
    Post-scoring citation. source_trust is platform-computed by
    TrustMatrixService.

    The agent's `score` node (Plan 10) is the only producer of Citation
    instances in non-test code. Tests can construct directly.
    """

    handle: EvidenceHandle
    excerpt: Annotated[str, StringConstraints(min_length=1)]
    source_trust: int = Field(ge=0, le=10)


class ResolvedCitation(BaseModel):
    """
    A Citation paired with the outcome of attempting to fetch its actual
    content.
    """

    citation: Citation
    resolved: bool
    matches_excerpt: bool
    actual_content: str | None


class DraftClaim(BaseModel):
    """
    LLM output. Caller must run TrustMatrixService.score_each() before passing
    to Verifier.
    """

    statement: str
    citations: list[DraftCitation] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    contradictions: list[DraftCitation] = Field(default_factory=list)

    @field_validator("statement")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("DraftClaim.statement cannot be empty")
        return v


class Claim(BaseModel):
    """
    The Verifier's input contract. Citations carry platform-owned source_trust.
    """

    statement: str
    citations: list[Citation] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    contradictions: list[Citation] = Field(default_factory=list)

    @field_validator("statement")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Claim.statement cannot be empty")
        return v


class Verdict(BaseModel):
    """
    The Verifier's output. PROVEN / UNKNOWN / REFUTED + reason + per-citation
    outcome.
    """

    status: VerdictStatus
    reason: str
    resolved_citations: list[ResolvedCitation]
