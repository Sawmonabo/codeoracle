"""MCPServer SDK registration and handler tests."""

from typing import Any, cast

import pytest
from mcp.server.lowlevel import Server

from codeoracle.core.config import Settings
from codeoracle.mcp.server import MCPServer
from codeoracle.models.query import Query, QueryResult, QueryStatus
from codeoracle.models.reindex import ReindexRequest
from codeoracle.models.snapshot import Snapshot, SnapshotStatus


class _FakeAgentClient:
    def __init__(self) -> None:
        self.queries: list[Query] = []

    async def run(self, query: Query) -> QueryResult:
        self.queries.append(query)
        return QueryResult(
            trace_id="trace-1",
            status=QueryStatus.FAILED,
            error=f"stubbed {query.symbol}",
        )


class _FakeIndexingService:
    def __init__(self) -> None:
        self.requests: list[ReindexRequest] = []

    async def enqueue_reindex(self, *, base_sha: str | None) -> Snapshot:
        self.requests.append(ReindexRequest(base_sha=base_sha))
        return Snapshot(sha="abcd1234", status=SnapshotStatus.PENDING)


def _server() -> tuple[MCPServer, _FakeAgentClient, _FakeIndexingService]:
    agent_client = _FakeAgentClient()
    indexing_service = _FakeIndexingService()
    server = MCPServer(
        agent_client=cast(Any, agent_client),
        indexing_service=cast(Any, indexing_service),
        settings=cast(Settings, object()),
    )
    return server, agent_client, indexing_service


async def test_server_uses_official_mcp_sdk_server() -> None:
    server, _, _ = _server()

    assert isinstance(server.server, Server)


async def test_list_tools_exposes_expected_contracts() -> None:
    server, _, _ = _server()

    tools = await server._list_tools()

    by_name = {tool.name: tool for tool in tools}
    explain_output_schema = by_name["explain_symbol"].outputSchema
    reindex_output_schema = by_name["reindex"].outputSchema
    assert set(by_name) == {"explain_symbol", "reindex"}
    assert explain_output_schema is not None
    assert reindex_output_schema is not None
    assert by_name["explain_symbol"].inputSchema["title"] == "Query"
    assert explain_output_schema["title"] == "QueryResult"
    assert by_name["reindex"].inputSchema["title"] == "ReindexRequest"
    assert reindex_output_schema["title"] == "Snapshot"


async def test_call_tool_explain_symbol_validates_and_delegates() -> None:
    server, agent_client, _ = _server()

    payload = await server._call_tool(
        "explain_symbol",
        {"type": "explain_symbol", "symbol": "pkg.mod.fn"},
    )

    assert payload["trace_id"] == "trace-1"
    assert payload["status"] == "FAILED"
    assert payload["error"] == "stubbed pkg.mod.fn"
    assert agent_client.queries == [
        Query(type="explain_symbol", symbol="pkg.mod.fn")
    ]


async def test_call_tool_reindex_validates_and_delegates() -> None:
    server, _, indexing_service = _server()

    payload = await server._call_tool("reindex", {"base_sha": "base123"})

    assert payload["sha"] == "abcd1234"
    assert payload["status"] == "pending"
    assert indexing_service.requests == [ReindexRequest(base_sha="base123")]


async def test_call_tool_rejects_unknown_tool() -> None:
    server, _, _ = _server()

    with pytest.raises(ValueError, match="Unknown MCP tool"):
        await server._call_tool("missing", {})
