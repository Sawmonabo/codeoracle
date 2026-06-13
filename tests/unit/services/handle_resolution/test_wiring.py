"""Plan 8 Task 13 — HandleResolverService full-wiring integration test.

Wires all 4 concrete strategies (with mocked adapters) into the service
and asserts:
  1. The strategy dict covers EVERY HandleType enum member — the
     `test_service_dispatch_covers_all_handle_types` invariant.
  2. Each HandleType dispatches to its registered resolver class.
  3. TRACE_SPAN and DB_RECORD route through StubResolver, raising
     UnsupportedHandleTypeError.
  4. CodeSpan, DocParagraph, GraphQuery dispatch correctly.
"""

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution import (
    CodeSpanResolver,
    DocParagraphResolver,
    GraphQueryResolver,
    HandleResolverService,
    StubResolver,
)


@pytest.fixture
def mock_sourcebot() -> AsyncMock:
    m = AsyncMock()
    m.fetch_span = AsyncMock(return_value="file content")
    return m


@pytest.fixture
def mock_graph_repo() -> AsyncMock:
    m = AsyncMock()
    m.execute_cypher = AsyncMock(return_value=[])
    return m


@pytest.fixture
def fully_wired_service(
    mock_sourcebot, mock_graph_repo
) -> HandleResolverService:
    """Mirrors the canonical composition root wiring (see Invariant 8)."""
    return HandleResolverService(
        strategies={
            HandleType.CODE_SPAN: CodeSpanResolver(sourcebot=mock_sourcebot),
            HandleType.DOC_PARAGRAPH: DocParagraphResolver(
                sourcebot=mock_sourcebot
            ),
            HandleType.GRAPH_QUERY: GraphQueryResolver(
                graph_repo=mock_graph_repo
            ),
            HandleType.TRACE_SPAN: StubResolver(
                handle_type=HandleType.TRACE_SPAN
            ),
            HandleType.DB_RECORD: StubResolver(
                handle_type=HandleType.DB_RECORD
            ),
        }
    )


def test_service_dispatch_covers_all_handle_types(
    fully_wired_service: HandleResolverService,
) -> None:
    """If a new HandleType is added in Plan 9+ but composition root forgets to
    wire it, this test fails. The 'why' for Invariant 8."""
    wired_types = fully_wired_service.registered_handle_types()
    enum_members = set(HandleType)
    missing = enum_members - wired_types
    assert not missing, (
        "HandleResolverService is not wired for HandleType members: "
        f"{missing}. Either register a real strategy or a StubResolver "
        "for these types."
    )


@pytest.mark.asyncio
async def test_code_span_dispatches_to_sourcebot(
    fully_wired_service: HandleResolverService, mock_sourcebot: AsyncMock
) -> None:
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://src/codeoracle/foo.py:1-2",
        snapshot_sha="abc1234",
    )
    result = await fully_wired_service.resolve(handle)
    assert result == "file content"
    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="src/codeoracle/foo.py",
        line_range=(1, 2),
        ref="abc1234",
    )


@pytest.mark.asyncio
async def test_doc_paragraph_dispatches_to_sourcebot(
    fully_wired_service: HandleResolverService, mock_sourcebot: AsyncMock
) -> None:
    handle = EvidenceHandle(
        type=HandleType.DOC_PARAGRAPH,
        locator="file://docs/architecture.md:42-50",
        snapshot_sha="abc1234",
    )
    result = await fully_wired_service.resolve(handle)
    assert result == "file content"


@pytest.mark.asyncio
async def test_graph_query_dispatches_to_graph_repo(
    fully_wired_service: HandleResolverService, mock_graph_repo: AsyncMock
) -> None:
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator="cypher://MATCH (n) RETURN n.fqn AS result",
        snapshot_sha="abc1234",
    )
    result = await fully_wired_service.resolve(handle)
    # No rows → "[]"
    assert result == "[]"
    mock_graph_repo.execute_cypher.assert_awaited_once()


@pytest.mark.asyncio
async def test_trace_span_routes_to_stub_resolver(
    fully_wired_service: HandleResolverService,
) -> None:
    handle = EvidenceHandle(
        type=HandleType.TRACE_SPAN,
        locator="trace://abc123",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError, match="TRACE_SPAN"):
        await fully_wired_service.resolve(handle)


@pytest.mark.asyncio
async def test_db_record_routes_to_stub_resolver(
    fully_wired_service: HandleResolverService,
) -> None:
    handle = EvidenceHandle(
        type=HandleType.DB_RECORD,
        locator="db://schema.users:42",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError, match="DB_RECORD"):
        await fully_wired_service.resolve(handle)


@pytest.mark.asyncio
async def test_no_handle_type_falls_through_to_dispatcher_default(
    fully_wired_service: HandleResolverService,
) -> None:
    """Belt-and-suspenders: if Invariant 8 ever drifts (someone removes
    StubResolver for DB_RECORD), the dispatcher's default branch still
    raises UnsupportedHandleTypeError. This test re-asserts that as a
    boundary against silent regressions."""
    # Construct a service intentionally missing GRAPH_QUERY.
    partial = HandleResolverService(
        strategies={
            HandleType.CODE_SPAN: CodeSpanResolver(
                sourcebot=AsyncMock(fetch_span=AsyncMock())
            ),
        }
    )
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator="cypher://MATCH (n) RETURN n",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await partial.resolve(handle)
    assert "GRAPH_QUERY" in str(exc.value)
