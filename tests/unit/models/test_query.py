"""Public query model tests."""

import pytest
from pydantic import ValidationError

from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult, QueryStatus


def _claim_and_verdict() -> tuple[Claim, Verdict]:
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://x:1-2",
        snapshot_sha="abc1234",
    )
    cit = Citation(handle=handle, excerpt="x", source_trust=8)
    claim = Claim(statement="s", citations=[cit], confidence=0.9)
    verdict = Verdict(
        status=VerdictStatus.PROVEN,
        reason="r",
        resolved_citations=[
            ResolvedCitation(
                citation=cit,
                resolved=True,
                matches_excerpt=True,
                actual_content="x",
            ),
        ],
    )
    return claim, verdict


def test_query_minimum():
    q = Query(
        symbol="codeoracle.services.verifier.service.VerifierService.verify"
    )
    assert q.type == "explain_symbol"
    assert q.corpus == "self"
    assert q.snapshot_sha is None


def test_query_symbol_required():
    with pytest.raises(ValidationError):
        Query(symbol="")


def test_query_result_proven_factory():
    claim, verdict = _claim_and_verdict()
    r = QueryResult.proven(trace_id="t1", claim=claim, verdict=verdict)
    assert r.status == QueryStatus.COMPLETE
    assert r.error is None


def test_query_result_failed_factory():
    r = QueryResult.failed(trace_id="t1", error="boom")
    assert r.status == QueryStatus.FAILED
    assert r.claim is None
    assert r.verdict is None
