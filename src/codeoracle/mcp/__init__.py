"""Model Context Protocol server process for CodeOracle.

This package implements the MCP server surface — `explain_symbol` and `reindex`
tools — over stdio (dev / IDE integration) and streamable HTTP (service-mode
parity with Plan 11's HTTP API). The full architectural invariants are in
docs/superpowers/plans/2026-05-21-mcp-server.md (invariants 1-12).

Public surface (re-exported for ergonomic imports):

- `MCPServer` — the OOP wrapper around `mcp.server.lowlevel.Server` that holds
  the composed dependencies (AgentClient + IndexingService + Settings) and
  registers the two tool handlers.
- `run_stdio` / `run_streamable_http` — the two transport runners; both accept
  an `MCPServer` instance constructed in `__main__.py`.
"""

from codeoracle.mcp.server import MCPServer
from codeoracle.mcp.transports import run_stdio, run_streamable_http

__all__ = ["MCPServer", "run_stdio", "run_streamable_http"]
