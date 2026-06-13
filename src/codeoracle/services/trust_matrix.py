"""TrustMatrixService — the platform's source-trust ranker.

Two responsibilities, both pure (no I/O):

1. `trust(handle_type, source_age_days)` — map an evidence kind and age to a
   0..10 integer trust score (architecture §16). A fresh source scores its
   base rank; a source older than 90 days decays by 2.
2. `score_each(draft)` — convert an LLM-produced `DraftClaim` into a
   Verifier-ready `Claim` by attaching the platform-computed `source_trust`
   to every citation and every contradiction. This is the *only* path from
   `DraftClaim` to `Claim`; `VerifierService.verify()` accepts only `Claim`,
   so the LLM can never forge a trust score (plan Invariant 2).
"""

from typing import ClassVar

from codeoracle.models.evidence import (
    Citation,
    Claim,
    DraftCitation,
    DraftClaim,
    HandleType,
)

_STALE_AGE_DAYS = 90
_STALE_TRUST_PENALTY = 2


class TrustMatrixService:
    """Maps (HandleType, age) to 0..10 trust, and converts DraftClaim to Claim.

    Stateless: no constructor, no injected dependencies. The composition root
    may share a single instance freely.
    """

    _BASE_RANKS: ClassVar[dict[HandleType, int]] = {
        HandleType.CODE_SPAN: 10,  # current code at the snapshot SHA
        HandleType.GRAPH_QUERY: 9,  # the platform's own extracted graph
        HandleType.TRACE_SPAN: 9,  # observed runtime behavior
        HandleType.DB_RECORD: 7,  # schema-bound
        HandleType.DOC_PARAGRAPH: 3,  # docs decay
    }

    def trust(self, handle_type: HandleType, source_age_days: int) -> int:
        """Return the 0..10 trust score for `handle_type` at `source_age_days`.

        A source older than `_STALE_AGE_DAYS` decays by `_STALE_TRUST_PENALTY`.
        The `max(0, ...)` floor keeps the result within
        `Citation.source_trust`'s
        `ge=0` bound even for a future low-rank HandleType (plan Invariant 10).
        """
        base = self._BASE_RANKS[handle_type]
        if source_age_days > _STALE_AGE_DAYS:
            return max(0, base - _STALE_TRUST_PENALTY)
        return base

    def score_each(
        self, draft: DraftClaim, *, source_age_days: int = 0
    ) -> Claim:
        """Convert an LLM `DraftClaim` into a Verifier-ready `Claim`.

        Attaches platform-computed `source_trust` to every citation and every
        contradiction. `statement` and `confidence` pass through unchanged.

        `source_age_days` defaults to 0 (current snapshot only) for the slice;
        multi-snapshot history (Phase 7+) will pass per-citation ages.
        """
        return Claim(
            statement=draft.statement,
            citations=[
                self._score(dc, source_age_days) for dc in draft.citations
            ],
            confidence=draft.confidence,
            contradictions=[
                self._score(dc, source_age_days) for dc in draft.contradictions
            ],
        )

    def _score(
        self, draft_citation: DraftCitation, source_age_days: int
    ) -> Citation:
        """
        Pair one `DraftCitation` with its platform-computed `source_trust`.
        """
        return Citation(
            handle=draft_citation.handle,
            excerpt=draft_citation.excerpt,
            source_trust=self.trust(
                draft_citation.handle.type, source_age_days
            ),
        )
