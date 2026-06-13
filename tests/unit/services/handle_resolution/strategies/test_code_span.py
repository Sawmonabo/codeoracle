"""Plan 8 Task 9 — CodeSpanResolver unit tests.

Pins:
  1. Parses file://path:start-end locator → SourcebotClient.fetch_span call.
  2. Passes handle.snapshot_sha (after head_sha_only stripping) as the
     `ref` arg to fetch_span.
  3. Returns the fetched content unchanged (no normalization at this layer
     — Plan 9 VerifierService owns normalization).
  4. Wraps SourcebotTerminalError as ResolverError (preserves cause chain).
  5. Wraps SourcebotTransientError as ResolverError too.
  6. Re-raises ValueError from parse_file_locator without wrapping (caller
     bug → bubble up; Verifier interprets ValueErrors as 500-class bugs).
  7. Strips the CodeOracle `+wt<12hex>` dirty-tree suffix from the snapshot
     SHA before calling fetch_span (G21 / M-8.1).
"""

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import (
    ResolverError,
    SourcebotTerminalError,
    SourcebotTransientError,
)
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.code_span import (
    CodeSpanResolver,
)


def _handle(locator: str, snapshot_sha: str = "abc1234") -> EvidenceHandle:
    return EvidenceHandle(
        type=HandleType.CODE_SPAN, locator=locator, snapshot_sha=snapshot_sha
    )


@pytest.fixture
def mock_sourcebot() -> AsyncMock:
    m = AsyncMock()
    m.fetch_span = AsyncMock(return_value="resolved content")
    return m


@pytest.mark.asyncio
async def test_resolve_calls_sourcebot_with_parsed_path_lines_and_ref(
    mock_sourcebot: AsyncMock,
) -> None:
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)
    handle = _handle(
        "file://src/codeoracle/foo.py:10-15", snapshot_sha="deadbee"
    )

    result = await resolver.resolve(handle)

    assert result == "resolved content"
    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="src/codeoracle/foo.py",
        line_range=(10, 15),
        ref="deadbee",
    )


@pytest.mark.asyncio
async def test_resolve_wraps_sourcebot_terminal_as_resolver_error(
    mock_sourcebot: AsyncMock,
) -> None:
    mock_sourcebot.fetch_span.side_effect = SourcebotTerminalError(
        "404 path not found"
    )
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)

    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(_handle("file://src/codeoracle/foo.py:10-15"))
    # Cause chain preserved — Verifier logs both layers
    assert isinstance(exc.value.__cause__, SourcebotTerminalError)
    assert "404 path not found" in str(exc.value.__cause__)


@pytest.mark.asyncio
async def test_resolve_wraps_sourcebot_transient_as_resolver_error(
    mock_sourcebot: AsyncMock,
) -> None:
    mock_sourcebot.fetch_span.side_effect = SourcebotTransientError("timeout")
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)

    with pytest.raises(ResolverError):
        await resolver.resolve(_handle("file://src/codeoracle/foo.py:10-15"))


@pytest.mark.asyncio
async def test_resolve_does_not_wrap_value_error_from_locator_parser(
    mock_sourcebot: AsyncMock,
) -> None:
    """parse_file_locator raises ValueError on a malformed locator.
    That's a caller-side bug (LLM emitted an invalid handle) — bubble it
    up; the Verifier surfaces it as 500-class so the agent learns its
    output schema is wrong."""
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)
    # Spec says EvidenceHandle.locator must include 'scheme://' — so a
    # bare 'src/foo.py:1-5' would fail Pydantic validation, not get here.
    # We test the next layer: a 'file://' locator with no line range.
    bad_handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        # Pydantic accepts the scheme; the parser rejects the missing line
        # range.
        locator="file://src/foo.py",
        snapshot_sha="abc1234",
    )

    with pytest.raises(ValueError, match="line range"):
        await resolver.resolve(bad_handle)

    # Confirm: fetch_span was NEVER called — parser short-circuits
    mock_sourcebot.fetch_span.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_does_not_normalize_returned_content(
    mock_sourcebot: AsyncMock,
) -> None:
    """If Sourcebot returns 'line 1\\nline 2', the resolver MUST return
    that exact string. Plan 9 VerifierService applies NFC + EOL + rstrip
    normalization at COMPARISON time, on BOTH sides. If we normalized
    here, the excerpt-vs-actual diff would lose information."""
    mock_sourcebot.fetch_span.return_value = (
        "   line with trailing spaces   \n\r\n"
    )
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)

    result = await resolver.resolve(_handle("file://x.py:1-1"))
    assert result == "   line with trailing spaces   \n\r\n"


@pytest.mark.asyncio
async def test_code_span_resolver_strips_wt_suffix_before_fetch(
    mock_sourcebot: AsyncMock,
) -> None:
    """CodeOracle dirty-tree SHAs (`<head>+wt<12hex>`) are NOT valid git refs.
    The resolver MUST strip the `+wt` suffix via `head_sha_only` before
    invoking the adapter; Sourcebot indexes only the head SHA. The adapter
    itself stays pure (Task 5 pins this) — the strip is a service-layer
    concern. Closes G21 / M-8.1."""
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)
    handle = _handle(
        "file://src/codeoracle/foo.py:10-15",
        snapshot_sha="abc123+wt0123456789ab",
    )

    await resolver.resolve(handle)

    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="src/codeoracle/foo.py",
        line_range=(10, 15),
        ref="abc123",
    )
