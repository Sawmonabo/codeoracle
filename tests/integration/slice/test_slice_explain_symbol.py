"""Plan 13 explain_symbol slice gates."""

import json
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from codeoracle.models.evidence import VerdictStatus
from codeoracle.models.query import QueryResult, QueryStatus
from tests.integration.slice._helpers import (
    assert_query_results_equal_modulo_trace_id,
    drive_reindex_to_applied,
    extract_handle_types,
)

_EXISTING_SYMBOL = (
    "codeoracle.services.verifier.service.VerifierService.verify"
)
_DELETED_SYMBOL = "codeoracle.deleted.symbol.DoesNotExist"
_MIN_PROVEN_CITATIONS = 2
_FORBIDDEN_HANDLE_TYPES = {"trace_span", "db_record"}


async def _api_explain_symbol(
    api_client: httpx.AsyncClient, *, symbol: str, snapshot_sha: str
) -> QueryResult:
    response = await api_client.post(
        "/v1/query",
        json={
            "type": "explain_symbol",
            "symbol": symbol,
            "corpus": "self",
            "snapshot_sha": snapshot_sha,
        },
    )
    assert response.status_code == 200, response.text
    return QueryResult.model_validate(response.json())


def _mcp_payload(result: Any) -> dict[str, Any]:
    if result.structuredContent is not None:
        return result.structuredContent
    assert result.content, "MCP tool returned no content"
    first = result.content[0]
    assert getattr(first, "type", None) == "text"
    return json.loads(first.text)


async def _mcp_explain_symbol(
    *, mcp_base_url: str, symbol: str, snapshot_sha: str
) -> QueryResult:
    async with (
        streamablehttp_client(mcp_base_url) as (
            read_stream,
            write_stream,
            _get_session_id,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "explain_symbol",
            {
                "type": "explain_symbol",
                "symbol": symbol,
                "corpus": "self",
                "snapshot_sha": snapshot_sha,
            },
        )
    assert result.isError is False
    return QueryResult.model_validate(_mcp_payload(result))


@pytest.mark.slice
async def test_explain_existing_symbol_returns_proven_with_citations(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    snapshot = await drive_reindex_to_applied(api_client, base_sha=None)

    result = await _api_explain_symbol(
        api_client,
        symbol=_EXISTING_SYMBOL,
        snapshot_sha=snapshot.sha,
    )

    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.PROVEN
    assert result.claim is not None
    assert len(result.claim.citations) >= _MIN_PROVEN_CITATIONS
    handle_types = extract_handle_types(result)
    assert len(handle_types) >= 2
    assert handle_types.isdisjoint(_FORBIDDEN_HANDLE_TYPES)


@pytest.mark.slice
async def test_explain_deleted_symbol_returns_unknown(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    snapshot = await drive_reindex_to_applied(api_client, base_sha=None)

    result = await _api_explain_symbol(
        api_client,
        symbol=_DELETED_SYMBOL,
        snapshot_sha=snapshot.sha,
    )

    assert result.status == QueryStatus.COMPLETE
    assert result.claim is None
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.UNKNOWN
    assert "symbol not found" in result.verdict.reason


@pytest.mark.slice
async def test_mcp_explain_symbol_matches_api_response(
    api_client: httpx.AsyncClient,
    mcp_base_url: str,
    clean_snapshot: None,
) -> None:
    snapshot = await drive_reindex_to_applied(api_client, base_sha=None)

    api_result = await _api_explain_symbol(
        api_client,
        symbol=_EXISTING_SYMBOL,
        snapshot_sha=snapshot.sha,
    )
    mcp_result = await _mcp_explain_symbol(
        mcp_base_url=mcp_base_url,
        symbol=_EXISTING_SYMBOL,
        snapshot_sha=snapshot.sha,
    )

    assert_query_results_equal_modulo_trace_id(
        api_result=api_result, mcp_result=mcp_result
    )
