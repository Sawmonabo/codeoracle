"""Plan 10 Task 8 — the score node: DraftClaim → Claim via the trust matrix."""

from codeoracle.agent.nodes.score import score_node
from codeoracle.models import (
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
)


def _draft() -> DraftClaim:
    cite = DraftCitation(
        handle=EvidenceHandle(
            type=HandleType.CODE_SPAN,
            locator="file://a.py:1-9",
            snapshot_sha="abc1234",
        ),
        excerpt="def f(): ...",
    )
    return DraftClaim(
        statement="It does X.",
        citations=[cite],
        confidence=0.8,
        contradictions=[],
    )


async def test_score_produces_a_claim(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps()
    update = await score_node(make_agent_state(draft_claim=_draft()), deps)
    claim = update["claim"]
    assert claim is not None
    assert claim.statement == "It does X."
    # the trust matrix stamps each citation with a source_trust the LLM cannot
    # forge
    citations = claim.citations
    assert all(c.source_trust >= 0 for c in citations)


async def test_score_errors_without_a_draft(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps()
    update = await score_node(make_agent_state(draft_claim=None), deps)
    assert "no draft claim" in str(update["error"])
