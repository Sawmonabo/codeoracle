"""MCP tool facade for CodeOracle.

This module keeps the slice's MCP contract small: the server exposes
`explain_symbol` through the shared AgentClient and `reindex` through the
IndexingService async enqueue path. Transport details live in transports.py.
"""

from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.core.config import Settings
from codeoracle.models.query import Query, QueryResult
from codeoracle.models.reindex import ReindexRequest
from codeoracle.models.snapshot import Snapshot
from codeoracle.services.indexing.service import IndexingService

_SERVER_NAME = "codeoracle-mcp"
_SERVER_VERSION = "0.1.0-slice"


class MCPServer:
    """CodeOracle MCP server facade."""

    def __init__(
        self,
        *,
        agent_client: AgentClient,
        indexing_service: IndexingService,
        settings: Settings,
    ) -> None:
        self._agent_client = agent_client
        self._indexing_service = indexing_service
        self._settings = settings
        self.server: Server[Any, Any] = Server(
            _SERVER_NAME,
            version=_SERVER_VERSION,
        )
        self.server.list_tools()(self._list_tools)
        self.server.call_tool()(self._call_tool)

    async def _list_tools(self) -> list[mcp_types.Tool]:
        """Return the public MCP tool definitions."""
        return [
            mcp_types.Tool(
                name="explain_symbol",
                description=(
                    "Run a CodeOracle explain_symbol query through the "
                    "agent process."
                ),
                inputSchema=Query.model_json_schema(),
                outputSchema=QueryResult.model_json_schema(),
            ),
            mcp_types.Tool(
                name="reindex",
                description=(
                    "Enqueue a CodeOracle reindex job and return the "
                    "snapshot receipt."
                ),
                inputSchema=ReindexRequest.model_json_schema(),
                outputSchema=Snapshot.model_json_schema(),
            ),
        ]

    async def _call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch one SDK tool call to the typed handler."""
        if name == "explain_symbol":
            return await self._handle_explain_symbol(arguments)
        if name == "reindex":
            return await self._handle_reindex(arguments)
        raise ValueError(f"Unknown MCP tool: {name}")

    async def _handle_explain_symbol(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate Query, delegate to the agent process, and return JSON."""
        query = Query.model_validate(arguments)
        result: QueryResult = await self._agent_client.run(query)
        return result.model_dump(mode="json")

    async def _handle_reindex(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Validate ReindexRequest, enqueue async reindex, and return Snapshot
        JSON.
        """
        request = ReindexRequest.model_validate(arguments)
        snapshot: Snapshot = await self._indexing_service.enqueue_reindex(
            base_sha=request.base_sha
        )
        return snapshot.model_dump(mode="json")
