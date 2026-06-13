"""Test the evidence model validators + type-system invariants."""

import pytest
from pydantic import ValidationError

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


def _handle(handle_type: HandleType = HandleType.CODE_SPAN) -> EvidenceHandle:
    return EvidenceHandle(
        type=handle_type,
        locator="file://src/codeoracle/services/verifier/service.py:14-38",
        snapshot_sha="abc1234",
    )


def test_evidence_handle_scheme_required():
    with pytest.raises(ValidationError):
        EvidenceHandle(
            type=HandleType.CODE_SPAN,
            locator="no-scheme:path",
            snapshot_sha="abc1234",
        )


def test_evidence_handle_scheme_accepted():
    h = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://path:1-10",
        snapshot_sha="abc1234",
    )
    assert h.locator == "file://path:1-10"


def test_evidence_handle_is_frozen():
    h = _handle()
    with pytest.raises(ValidationError):
        h.locator = "file://other:1-10"


def test_evidence_handle_snapshot_sha_min_length():
    with pytest.raises(ValidationError):
        EvidenceHandle(
            type=HandleType.CODE_SPAN,
            locator="file://x:1-2",
            snapshot_sha="abc",
        )


def test_draft_claim_empty_statement_rejected():
    with pytest.raises(ValidationError):
        DraftClaim(
            statement="   ",
            citations=[DraftCitation(handle=_handle(), excerpt="...")],
            confidence=0.9,
        )


def test_draft_claim_requires_at_least_one_citation():
    with pytest.raises(ValidationError):
        DraftClaim(statement="ok", citations=[], confidence=0.9)


def test_claim_confidence_bounds():
    with pytest.raises(ValidationError):
        Claim(
            statement="ok",
            citations=[
                Citation(handle=_handle(), excerpt="...", source_trust=10)
            ],
            confidence=1.1,
        )
    with pytest.raises(ValidationError):
        Claim(
            statement="ok",
            citations=[
                Citation(handle=_handle(), excerpt="...", source_trust=10)
            ],
            confidence=-0.1,
        )


def test_citation_source_trust_bounds():
    with pytest.raises(ValidationError):
        Citation(handle=_handle(), excerpt="...", source_trust=11)
    with pytest.raises(ValidationError):
        Citation(handle=_handle(), excerpt="...", source_trust=-1)


def test_draft_claim_cannot_satisfy_verifier_signature():
    """
    Type-system invariant: passing a DraftClaim where a Claim is expected is a
    type error.

    Pydantic itself is duck-typed at runtime, but the Verifier's signature
    accepts only Claim,
    and DraftCitation lacks source_trust — so a DraftClaim cannot be reused as
    a Claim without going through TrustMatrixService.score_each().
    """
    draft = DraftClaim(
        statement="ok",
        citations=[DraftCitation(handle=_handle(), excerpt="x")],
        confidence=0.9,
    )
    # Pydantic dump confirms the shape difference: DraftClaim's citations have
    # no source_trust
    payload = draft.model_dump()
    assert "source_trust" not in payload["citations"][0]


def test_verdict_construction():
    citation = Citation(handle=_handle(), excerpt="x", source_trust=8)
    resolved = ResolvedCitation(
        citation=citation,
        resolved=True,
        matches_excerpt=True,
        actual_content="x",
    )
    v = Verdict(
        status=VerdictStatus.PROVEN,
        reason="all good",
        resolved_citations=[resolved],
    )
    assert v.status == VerdictStatus.PROVEN
    assert len(v.resolved_citations) == 1
