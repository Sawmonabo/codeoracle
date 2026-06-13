"""VerifierService — converts a trust-scored Claim into a PROVEN / UNKNOWN /
REFUTED Verdict.

The Verifier is the platform's "prove, don't guess" mechanism. It takes a
`Claim` whose citations already carry platform-owned `source_trust` (attached
by `TrustMatrixService.score_each()`), resolves every citation's handle to
live content via the injected `HandleResolverService`, and — after
normalization — byte-compares the live content against the LLM's claimed
excerpt:

  - confidence below `PROVEN_CONFIDENCE_FLOOR`  -> UNKNOWN  (abstain)
  - any citation has an empty excerpt           -> UNKNOWN  (no evidence)
  - any citation fails to resolve               -> UNKNOWN  (missing evidence)
  - any resolved citation's content has drifted -> REFUTED  (drift / tampering)
  - otherwise                                   -> PROVEN

UNKNOWN is checked before REFUTED: a missing citation means the platform lacks
the evidence to make the strong "disproven" claim (plan Invariant 4).
"""

import asyncio
import unicodedata
from typing import Protocol

from codeoracle.core.exceptions import ResolverError
from codeoracle.core.logging import get_audit_logger
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)

# The citation-resolution outcome line is an *audit* record (spec §8.5 crit.
# 11),
# not routine debug. It must survive at any operational verbosity — including a
# production `log_level` of WARNING/ERROR, at which the global filtering
# wrapper
# (`make_filtering_bound_logger(log_level)`, Plan 3 L959) would drop a plain
# `info`/`warning` line. `get_audit_logger` returns a logger whose floor is
# fixed
# at INFO independent of `settings.log_level`, so all three outcomes (info /
# warning / error) are always emitted while still flowing through the global
# processor chain (`_redact_secrets`, renderer) and factory. See Deviation 4.
_AUDIT = get_audit_logger(__name__)

# Below this confidence, verify() returns UNKNOWN regardless of how citations
# resolve. Without a floor, an LLM that returned confidence=0.05 with one
# accidentally resolvable citation would be reported PROVEN — a structural
# lie. Initial value 0.6; raise later from slice calibration data (spec §6.2).
PROVEN_CONFIDENCE_FLOOR = 0.6


class CitationResolverProtocol(Protocol):
    async def resolve(self, handle: EvidenceHandle) -> str: ...


def _normalize_for_comparison(text: str) -> str:
    """Return `text` in a canonical form for excerpt comparison.

    Applies, in order: NFC Unicode normalization, CRLF/CR -> LF line endings,
    per-line trailing-whitespace strip, and an outer strip. Applied to *both*
    sides of an excerpt comparison so the Verifier does not flake on cosmetic
    drift. It does NOT absorb meaningful drift — different characters still
    compare unequal, so REFUTED still fires for real tampering (Invariant 6).
    """
    nfc = unicodedata.normalize("NFC", text)
    lf = nfc.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in lf.split("\n")).strip()


class VerifierService:
    """Validates a `Claim`, returning a PROVEN / UNKNOWN / REFUTED `Verdict`.

    Pure-domain: the only external effect is the injected
    `HandleResolverService`.
    The service is stateless — construct once at the composition root and
    reuse.
    """

    def __init__(self, *, resolver: CitationResolverProtocol) -> None:
        self._resolver = resolver

    async def verify(self, claim: Claim) -> Verdict:
        """Resolve every citation on `claim` and render a `Verdict`.

        Two pre-resolution short-circuits return `UNKNOWN` without resolving
        anything — confidence below the floor, and any empty-excerpt citation;
        the module docstring lists the full verdict precedence. Otherwise,
        citations are resolved concurrently. `claim.contradictions` are NOT
        resolved here — they are scored evidence for downstream display, not
        inputs to this claim's verdict.
        """
        if claim.confidence < PROVEN_CONFIDENCE_FLOOR:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=(
                    f"claim confidence {claim.confidence:.2f} is below the "
                    f"PROVEN floor {PROVEN_CONFIDENCE_FLOOR}"
                ),
                resolved_citations=[],
            )

        evidence_free = [
            c
            for c in claim.citations
            if not _normalize_for_comparison(c.excerpt)
        ]
        if evidence_free:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=(
                    f"{len(evidence_free)} of {len(claim.citations)} "
                    "citations carry an empty excerpt — no checkable "
                    "evidence"
                ),
                resolved_citations=[],
            )

        resolved: list[ResolvedCitation] = list(
            await asyncio.gather(
                *(self._resolve_one(c) for c in claim.citations)
            )
        )

        unresolved = [rc for rc in resolved if not rc.resolved]
        if unresolved:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=(
                    f"{len(unresolved)} of {len(resolved)} citations "
                    "failed to resolve"
                ),
                resolved_citations=resolved,
            )

        mismatched = [rc for rc in resolved if not rc.matches_excerpt]
        if mismatched:
            return Verdict(
                status=VerdictStatus.REFUTED,
                reason=(
                    f"{len(mismatched)} of {len(resolved)} citations "
                    "have content that drifted from the cited excerpt"
                ),
                resolved_citations=resolved,
            )

        return Verdict(
            status=VerdictStatus.PROVEN,
            reason=(
                f"all {len(resolved)} citations resolved and match "
                "their excerpts"
            ),
            resolved_citations=resolved,
        )

    async def _resolve_one(self, citation: Citation) -> ResolvedCitation:
        """
        Resolve one citation's handle and compare live content to its excerpt.

        Only `ResolverError` (and its subclasses) is caught — that is an
        expected resolution failure. Any other exception is a bug and
        propagates uncaught (plan Invariant 5). The `try` wraps only the
        resolver call, so the `except` cannot mask a defect in normalization
        or in `ResolvedCitation` construction.

        Emits exactly one structured log line per call (spec §8.5 crit. 11) on
        the dedicated `_AUDIT` channel (fixed-INFO floor, immune to the global
        `log_level` filter): `info` when resolved and matching, `error` when
        resolved but drifted, `warning` when unresolved. Using `_AUDIT` rather
        than a `log_level`-filtered logger keeps the audit line satisfiable at
        every prod verbosity — including `log_level=WARNING` (drops `info`) and
        `log_level=ERROR` (also drops `warning`) — for all three outcomes.
        """
        try:
            actual = await self._resolver.resolve(citation.handle)
        except ResolverError as exc:
            _AUDIT.warning(
                "verifier.citation_unresolved",
                locator=citation.handle.locator,
                handle_type=citation.handle.type.value,
                error=str(exc),
            )
            return ResolvedCitation(
                citation=citation,
                resolved=False,
                matches_excerpt=False,
                actual_content=None,
            )

        matches = _normalize_for_comparison(
            actual
        ) == _normalize_for_comparison(citation.excerpt)
        if matches:
            _AUDIT.info(
                "verifier.citation_resolved",
                locator=citation.handle.locator,
                handle_type=citation.handle.type.value,
            )
        else:
            _AUDIT.error(
                "verifier.citation_excerpt_mismatch",
                locator=citation.handle.locator,
                handle_type=citation.handle.type.value,
            )
        return ResolvedCitation(
            citation=citation,
            resolved=True,
            matches_excerpt=matches,
            actual_content=actual,
        )
