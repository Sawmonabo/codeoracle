"""Integration test — VerifierService wired to a real HandleResolverService.

Confirms the public re-export path (`codeoracle.services.verifier`) and that
the two services compose: real HandleResolverService dispatch + real
VerifierService verdict logic, with only the leaf resolver strategy faked.
"""

import asyncio

import pytest

from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    VerdictStatus,
)
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.verifier import (
    PROVEN_CONFIDENCE_FLOOR,
    VerifierService,
)


class _StubStrategy:
    """
    Duck-types HandleResolverStrategy — returns a fixed string for any handle.
    """

    def __init__(self, content: str) -> None:
        self._content = content

    async def resolve(self, handle: EvidenceHandle) -> str:
        await asyncio.sleep(0)
        return self._content


def _claim(handle: EvidenceHandle, excerpt: str) -> Claim:
    return Claim(
        statement="a statement under test",
        citations=[Citation(handle=handle, excerpt=excerpt, source_trust=10)],
        confidence=0.9,
    )


def test_public_reexports_are_importable() -> None:
    """VerifierService and PROVEN_CONFIDENCE_FLOOR are reachable from the
    package root — not only from the .service submodule."""
    assert PROVEN_CONFIDENCE_FLOOR == 0.6
    assert VerifierService.__name__ == "VerifierService"


@pytest.mark.asyncio
async def test_verifier_composes_with_handle_resolver_service() -> None:
    """End-to-end: a real HandleResolverService dispatches a CODE_SPAN handle
    to a stub strategy, and VerifierService.verify renders PROVEN."""
    resolver = HandleResolverService(
        strategies={HandleType.CODE_SPAN: _StubStrategy("x = 1")}
    )
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://app/x.py:1-2",
        snapshot_sha="abcd1234",
    )
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(handle, "x = 1")
    )
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verifier_renders_unknown_for_unsupported_handle_type() -> None:
    """A real HandleResolverService raises UnsupportedHandleTypeError for an
    unregistered handle type; VerifierService catches it (it is a
    ResolverError subclass) and renders UNKNOWN — not an uncaught crash. This
    exercises the Plan 8 / Plan 9 exception contract with real classes."""
    resolver = HandleResolverService(
        strategies={HandleType.CODE_SPAN: _StubStrategy("x = 1")}
    )
    handle = EvidenceHandle(
        type=HandleType.DOC_PARAGRAPH,  # NOT registered in `strategies`
        locator="doc://readme.md#intro",
        snapshot_sha="abcd1234",
    )
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(handle, "hello")
    )
    assert verdict.status == VerdictStatus.UNKNOWN
