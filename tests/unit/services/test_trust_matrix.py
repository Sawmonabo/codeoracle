"""
Unit tests for TrustMatrixService — trust() ranking and DraftClaim -> Claim.
"""

import pytest

from codeoracle.models.evidence import (
    Claim,
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
)
from codeoracle.services.trust_matrix import TrustMatrixService


@pytest.fixture
def svc() -> TrustMatrixService:
    return TrustMatrixService()


# --- local builders ---------------------------------------------------------


def _handle(handle_type=HandleType.CODE_SPAN):
    return EvidenceHandle(
        type=handle_type, locator="file://x.py:1-2", snapshot_sha="abcd1234"
    )


def _draft_citation(handle_type=HandleType.CODE_SPAN, excerpt="x = 1"):
    return DraftCitation(handle=_handle(handle_type), excerpt=excerpt)


# --- trust() ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("handle_type", "expected"),
    [
        (HandleType.CODE_SPAN, 10),
        (HandleType.GRAPH_QUERY, 9),
        (HandleType.TRACE_SPAN, 9),
        (HandleType.DB_RECORD, 7),
        (HandleType.DOC_PARAGRAPH, 3),
    ],
)
def test_trust_fresh_source_uses_base_rank(
    svc: TrustMatrixService, handle_type: HandleType, expected: int
) -> None:
    """A source aged 0 days scores its full base rank."""
    assert svc.trust(handle_type, source_age_days=0) == expected


@pytest.mark.parametrize(
    ("handle_type", "expected"),
    [
        (HandleType.CODE_SPAN, 8),
        (HandleType.GRAPH_QUERY, 7),
        (HandleType.TRACE_SPAN, 7),
        (HandleType.DB_RECORD, 5),
        (HandleType.DOC_PARAGRAPH, 1),
    ],
)
def test_trust_stale_source_decays_by_two(
    svc: TrustMatrixService, handle_type: HandleType, expected: int
) -> None:
    """A source older than 90 days loses _STALE_TRUST_PENALTY (2) points."""
    assert svc.trust(handle_type, source_age_days=120) == expected


def test_trust_staleness_boundary_is_strict_greater_than(
    svc: TrustMatrixService,
) -> None:
    """
    Exactly 90 days is NOT stale; 91 days is. The check is `> 90`, not `>= 90`.
    """
    assert svc.trust(HandleType.CODE_SPAN, source_age_days=90) == 10
    assert svc.trust(HandleType.CODE_SPAN, source_age_days=91) == 8


def test_trust_never_returns_negative(svc: TrustMatrixService) -> None:
    """trust() must satisfy Citation.source_trust's `ge=0` bound for every
    HandleType, even at an extreme age. The `max(0, ...)` floor guards this."""
    for handle_type in HandleType:
        assert svc.trust(handle_type, source_age_days=10_000) >= 0


def test_every_handle_type_has_a_base_rank(svc: TrustMatrixService) -> None:
    """Adding a HandleType without a _BASE_RANKS entry must fail loudly.
    trust() does a dict lookup keyed by handle type; a missing key raises
    KeyError. Iterating the full enum here pins that coverage without
    reaching into the private _BASE_RANKS table."""
    for handle_type in HandleType:
        assert isinstance(svc.trust(handle_type, source_age_days=0), int)


# --- score_each() / DraftClaim -> Claim -------------------------------------


def test_score_each_returns_a_claim(svc: TrustMatrixService) -> None:
    """score_each produces a Claim — the only type VerifierService accepts."""
    draft = DraftClaim(
        statement="the answer", citations=[_draft_citation()], confidence=0.9
    )
    assert isinstance(svc.score_each(draft), Claim)


def test_score_each_attaches_source_trust_to_citations(
    svc: TrustMatrixService,
) -> None:
    """Each citation gets the platform-computed trust for its handle type."""
    draft = DraftClaim(
        statement="the answer",
        citations=[_draft_citation(HandleType.CODE_SPAN)],
        confidence=0.9,
    )
    claim = svc.score_each(draft)
    assert [c.source_trust for c in claim.citations] == [10]


def test_score_each_passes_statement_and_confidence_through(
    svc: TrustMatrixService,
) -> None:
    """
    statement and confidence are copied verbatim — TrustMatrix only adds trust.
    """
    draft = DraftClaim(
        statement="unchanged statement",
        citations=[_draft_citation()],
        confidence=0.73,
    )
    claim = svc.score_each(draft)
    assert claim.statement == "unchanged statement"
    assert claim.confidence == 0.73


def test_score_each_scores_contradictions_too(svc: TrustMatrixService) -> None:
    """
    contradictions is a separate list — score_each MUST score it, not drop it.
    """
    draft = DraftClaim(
        statement="claimed",
        citations=[_draft_citation(HandleType.CODE_SPAN)],
        confidence=0.8,
        contradictions=[_draft_citation(HandleType.DOC_PARAGRAPH)],
    )
    claim = svc.score_each(draft)
    assert [c.source_trust for c in claim.contradictions] == [3]


def test_score_each_default_source_age_is_zero(
    svc: TrustMatrixService,
) -> None:
    """With no source_age_days argument, citations are scored as fresh."""
    draft = DraftClaim(
        statement="fresh",
        citations=[_draft_citation(HandleType.DOC_PARAGRAPH)],
        confidence=0.9,
    )
    assert svc.score_each(draft).citations[0].source_trust == 3


def test_score_each_applies_staleness_to_citations_and_contradictions(
    svc: TrustMatrixService,
) -> None:
    """source_age_days decays every scored citation, on both lists."""
    draft = DraftClaim(
        statement="old",
        citations=[_draft_citation(HandleType.CODE_SPAN)],
        confidence=0.9,
        contradictions=[_draft_citation(HandleType.GRAPH_QUERY)],
    )
    claim = svc.score_each(draft, source_age_days=200)
    assert claim.citations[0].source_trust == 8  # 10 - 2
    assert claim.contradictions[0].source_trust == 7  # 9 - 2


def test_score_each_preserves_handle_and_excerpt(
    svc: TrustMatrixService,
) -> None:
    """_score copies handle and excerpt verbatim; only source_trust is new."""
    draft = DraftClaim(
        statement="verbatim",
        citations=[
            _draft_citation(HandleType.CODE_SPAN, excerpt="def f(): pass")
        ],
        confidence=0.9,
    )
    citation = svc.score_each(draft).citations[0]
    assert citation.excerpt == "def f(): pass"
    assert citation.handle.type == HandleType.CODE_SPAN
