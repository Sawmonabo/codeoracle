"""Plan 8 Task 10 — DocParagraphResolver unit tests.

DocParagraph and CodeSpan resolvers share the same locator format and
underlying I/O — but they MUST be distinct classes (spec §3.2) so
TrustMatrixService can score by HandleType.

Tests pin:
  1. Same locator parsing + SourcebotClient call shape as CodeSpan.
  2. Accepts a `.py` path under `doc_paragraph` type — type does NOT
     constrain file extension (spec §6.1 example uses service.py for
     DOC_PARAGRAPH).
  3. Same SourcebotError → ResolverError wrapping.
  4. Resolver does NOT branch on HandleType internally — its sole purpose
     is the doc-flavored I/O path.
  5. Strips the CodeOracle `+wt<12hex>` dirty-tree suffix from the snapshot
     SHA before calling fetch_span (G21 / M-8.1) — same behavior as
     CodeSpanResolver.
"""

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import ResolverError, SourcebotTerminalError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.doc_paragraph import (
    DocParagraphResolver,
)


def _handle(locator: str, snapshot_sha: str = "abc1234") -> EvidenceHandle:
    return EvidenceHandle(
        type=HandleType.DOC_PARAGRAPH,
        locator=locator,
        snapshot_sha=snapshot_sha,
    )


@pytest.fixture
def mock_sourcebot() -> AsyncMock:
    m = AsyncMock()
    m.fetch_span = AsyncMock(return_value="doc content")
    return m


@pytest.mark.asyncio
async def test_resolve_calls_sourcebot_with_path_lines_and_ref(
    mock_sourcebot: AsyncMock,
) -> None:
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    handle = _handle(
        "file://docs/architecture.md:42-50", snapshot_sha="abc1234"
    )

    result = await resolver.resolve(handle)

    assert result == "doc content"
    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="docs/architecture.md",
        line_range=(42, 50),
        ref="abc1234",
    )


@pytest.mark.asyncio
async def test_resolve_accepts_python_path_under_doc_paragraph_type(
    mock_sourcebot: AsyncMock,
) -> None:
    """Spec line 1290 example uses 'file://src/.../service.py:15-20' under
    DOC_PARAGRAPH (the LLM is citing a docstring inside a code file).
    The resolver MUST NOT reject this."""
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    handle = _handle(
        "file://src/codeoracle/services/verifier/service.py:15-20"
    )
    result = await resolver.resolve(handle)
    assert result == "doc content"
    mock_sourcebot.fetch_span.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_wraps_sourcebot_error_as_resolver_error(
    mock_sourcebot: AsyncMock,
) -> None:
    mock_sourcebot.fetch_span.side_effect = SourcebotTerminalError("404")
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(_handle("file://docs/foo.md:1-2"))
    assert isinstance(exc.value.__cause__, SourcebotTerminalError)


@pytest.mark.asyncio
async def test_doc_paragraph_resolver_strips_wt_suffix_before_fetch(
    mock_sourcebot: AsyncMock,
) -> None:
    """Same +wt-stripping behavior as CodeSpanResolver — DocParagraph
    citations may carry the CodeOracle dirty-tree suffix and Sourcebot does
    not understand it. The strip happens in the resolver (via
    `head_sha_only`), keeping the adapter layer-pure. Closes G21 / M-8.1."""
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    handle = _handle(
        "file://docs/architecture.md:42-50",
        snapshot_sha="abc123+wt0123456789ab",
    )

    await resolver.resolve(handle)

    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="docs/architecture.md",
        line_range=(42, 50),
        ref="abc123",
    )
