"""§8.5 gate 3 — VerifierService.verify returns REFUTED when a citation's
excerpt does not match what the resolver fetches from Sourcebot.

Why this test bypasses the API/agent surface: under Plan 10 Invariant 1
and Plan 9's contract, citations are always constructed from
HandleResolverService.resolve() output. Any API-driven query produces
citation.excerpt == resolver_output by construction, so the REFUTED
branch is unreachable through the API in steady state — that is by
design. Driving REFUTED therefore requires hand-constructing a
mismatched Citation and calling VerifierService.verify directly against
the live compose-deployed Sourcebot. The test imports VerifierService,
HandleResolverService, CodeSpanResolver, and SourcebotClient from the
in-tree src/codeoracle/ package; the compose stack is up (compose_stack
fixture); HTTP traffic goes to the compose Sourcebot at host-port 3000.
"""

import subprocess

import pytest

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    VerdictStatus,
)
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.handle_resolution.strategies.code_span import (
    CodeSpanResolver,
)
from codeoracle.services.verifier.service import VerifierService

# A real, in-tree file with content stable enough that a fixture
# excerpt would be brittle — we don't bundle a "matching" excerpt; we
# only need to know the file EXISTS at a real handle so the resolver
# call succeeds (resolved=True) and we can compare against a
# DELIBERATE non-match. codeoracle/__init__.py is < 50 lines and changes
# infrequently; if it is renamed/removed, this assertion surfaces
# the regression as resolved=False on the first run.
_SLICE_ANCHOR_LOCATOR = "file://src/codeoracle/__init__.py:1-3"
_DELIBERATE_WRONG_EXCERPT = (
    "DELIBERATE_NON_MATCHING_EXCERPT_ZZZ_NOT_PRESENT_IN_REPO_xyz123"
)


def _current_snapshot_sha() -> str:
    """Return host repo HEAD — Sourcebot indexes the host bind-mount at
    compose-up, so the host HEAD is the SHA Sourcebot has indexed for
    the duration of the slice session (pytest-docker holds the compose
    stack session-wide, and the workspace HEAD does not change while
    the session is running). We resolve this at test time rather than
    via a fixture so the test module is fully self-contained.
    """
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()


@pytest.mark.slice
async def test_verifier_returns_refuted_when_excerpt_mismatches_source(
    compose_stack: None,
) -> None:
    """§8.5 gate 3 — Verifier emits REFUTED when citation excerpt
    mismatches resolved source.

    Path: a hand-built Citation with a deliberately wrong `excerpt`
    against a real, indexable handle. VerifierService.verify resolves
    via SourcebotClient (HTTP to compose Sourcebot @ :3000), compares
    returned bytes to the wrong excerpt, and produces:

        ResolvedCitation(resolved=True, matches_excerpt=False)

    which Plan 9's verdict aggregation classifies as REFUTED.

    Signature pins (advisor round-3 grep verification, 2026-05-26):
      * EvidenceHandle has fields {type, locator, snapshot_sha}
        (Plan 3 L1253-1260). Citation embeds an EvidenceHandle;
        HandleType lives on the handle, NOT on Citation.
      * Citation has fields {handle, excerpt, source_trust}
        (Plan 3 L1277-1286). Constructing with a `handle_type=` kwarg
        is a TypeError.
      * HandleResolverService takes `strategies: dict[HandleType, ...]`
        (Plan 8 L7 + L2047). There is no `resolvers=` kwarg and no
        `build_resolver_map` helper — strategies dict is constructed
        inline at each call site, matching the unit tests in Plan 9.
      * VerifierService takes `*, resolver: HandleResolverService`
        (Plan 9 L1411; ALL Plan 9 tests use `VerifierService(resolver=...)`).
        `handle_resolver=` is a TypeError.
      * Claim has fields {statement, citations, confidence,
        contradictions} (Plan 3 L1314-1320). Constructing with `text=`
        is a TypeError; the field is `statement`.
      * Verdict.status, NOT .kind (Plan 3 L1333 + Plan 9 ~25 call sites
        all using `verdict.status`).
    """
    snapshot_sha = _current_snapshot_sha()

    # Build the production resolver layer against the compose Sourcebot,
    # then wrap it in the production VerifierService — no mocks.
    #
    # Composition note: `Settings` (Plan 3 L503) does NOT carry a `sourcebot:
    # SourcebotConfig` field — `SourcebotConfig` (Plan 8 L529) is a standalone
    # BaseSettings each composition root constructs directly. Production sites
    # (Plan 8 L434/444/454/461/467) build SourcebotConfig from env,
    # letting Pydantic BaseSettings read `CODEORACLE_SOURCEBOT__BASE_URL`,
    # `CODEORACLE_SOURCEBOT__API_KEY`, `CODEORACLE_SOURCEBOT__DEFAULT_REPO`.
    # The `compose_stack` fixture (Task 2) ensures Plan 2's `.env` is exported
    # to the host pytest process before any slice test runs, so this no-arg
    # construction resolves against the live compose Sourcebot at
    # `http://127.0.0.1:3000`. SourcebotClient takes the config positionally
    # (Plan 8 L827: `def __init__(self, config: SourcebotConfig) -> None`).
    sourcebot_client = SourcebotClient(SourcebotConfig.model_validate({}))
    handle_resolver = HandleResolverService(
        strategies={
            HandleType.CODE_SPAN: CodeSpanResolver(sourcebot=sourcebot_client),
        },
    )
    verifier = VerifierService(resolver=handle_resolver)

    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator=_SLICE_ANCHOR_LOCATOR,
        snapshot_sha=snapshot_sha,
    )
    citation = Citation(
        handle=handle,
        excerpt=_DELIBERATE_WRONG_EXCERPT,
        source_trust=5,
    )
    claim = Claim(
        statement="(slice gate 3) verifier should REFUTE this claim",
        citations=[citation],
        confidence=0.9,
    )

    verdict = await verifier.verify(claim)

    assert verdict.status == VerdictStatus.REFUTED, (
        f"§8.5 gate 3: expected REFUTED, got {verdict.status} "
        f"(reason={verdict.reason!r})"
    )
    # Plan 9 contract — REFUTED <=> >=1 ResolvedCitation has
    # resolved=True AND matches_excerpt=False (Plan 9 L1459, L1462).
    assert len(verdict.resolved_citations) == 1, (
        "Expected exactly one resolved citation; got "
        f"{len(verdict.resolved_citations)}"
    )
    rc = verdict.resolved_citations[0]
    assert rc.resolved is True, (
        "Resolver should have succeeded (the handle is valid); "
        f"resolved={rc.resolved}"
    )
    assert rc.matches_excerpt is False, (
        "The excerpt was deliberately wrong; matches_excerpt should "
        f"be False, got {rc.matches_excerpt}"
    )
