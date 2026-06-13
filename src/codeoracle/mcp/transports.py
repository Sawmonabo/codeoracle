# src/codeoracle/mcp/transports.py
"""Transport runners for the MCPServer.

Two transports share the same MCPServer instance:

- `run_stdio(server)` — stdio JSON-RPC (dev mode / IDE integration).
  The MCP client (Claude Code, Cursor) launches the `codeoracle-mcp` binary
  as a subprocess and exchanges JSON-RPC over stdin/stdout. This runner
  binds the SDK's stdio_server context manager + delegates to
  Server.run(read_stream, write_stream).

- `run_streamable_http(server, *, host, port)` — streamable HTTP
  (service mode / §8.5 parity gate). Constructs a
  `StreamableHTTPSessionManager` over the same `Server` instance, wraps
  its `run()` context in a Starlette `lifespan`, mounts the request
  handler at `/mcp`, and serves the ASGI app via `uvicorn.run`. The
  SDK does NOT expose a one-shot `Server.streamable_http_app(...)`
  factory in v1.12.4 (verified against the SDK source) — the caller
  composes Starlette + the session manager directly.

The stdout-pollution invariant (Plan 12 invariant 5) is enforced
elsewhere — `__main__.py` calls configure_logging(stream=sys.stderr)
BEFORE invoking run_stdio. The transport runners themselves do not
reconfigure logging.

The HTTP transport is stateless (`stateless=True` on the session
manager) per DECIDE-H — both tools are single-shot request/response,
no session state to preserve.
"""

import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import uvicorn
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

if TYPE_CHECKING:
    from codeoracle.mcp.server import MCPServer


SERVER_VERSION = "0.1.0-slice"


def build_initialization_options(sdk_server: Server) -> InitializationOptions:
    """SDK initialization metadata announced to the MCP client on connect.

    The `capabilities` field is built via the SDK's own
    `Server.get_capabilities()` helper — the canonical pattern in v1.12.4
    (verified against the lowlevel-server lifespan example in the SDK
    README). Hand-rolling the dict (e.g., `{"tools": {"listChanged": False}}`)
    is brittle: the SDK helper enforces the exact ServerCapabilities Pydantic
    shape, picks defaults consistent with what the registered handlers
    actually support, and adapts when the schema evolves.

    Public (no leading underscore) so the Task 7 integration tests can
    import this directly without reaching for a private symbol.
    """
    return InitializationOptions(
        server_name="codeoracle-mcp",
        server_version=SERVER_VERSION,
        capabilities=sdk_server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
    )


async def run_stdio(mcp_server: MCPServer) -> None:
    """Run the MCP server over stdio JSON-RPC.

    Binds stdin/stdout via the SDK's stdio_server context manager;
    blocks until the client disconnects.

    Stdout-pollution warning (Plan 12 invariant 5): the CALLER
    (__main__.py) MUST configure logging to stderr BEFORE invoking this
    function. Once stdio_server is entered, any byte written to stdout
    that isn't a properly-framed JSON-RPC message corrupts the protocol
    and silently desyncs the MCP client.

    SDK exception semantics: `Server.run` defaults `raise_exceptions=False`
    in v1.12.4 — handler exceptions are caught by the SDK and converted to
    `CallToolResult(isError=True)` per `_make_error_result`. We rely on
    that default (DO NOT override to True) — overriding causes handler
    exceptions to propagate up through `Server.run`, which in stdio mode
    means a process crash mid-session rather than a wire-level isError
    response the client can act on.
    """
    sdk_server = mcp_server.server
    async with stdio_server() as (read_stream, write_stream):
        await sdk_server.run(
            read_stream,
            write_stream,
            build_initialization_options(sdk_server),
        )


def _build_streamable_http_app(mcp_server: MCPServer) -> Starlette:
    """Compose the Starlette ASGI app that wires the SDK session manager.

    Why this exists: the v1.12.4 SDK does NOT expose a one-shot
    `Server.streamable_http_app(...)` factory. The canonical pattern
    (per the SDK's own `examples/servers/simple-streamablehttp`) is for
    the caller to:

    1. Construct `StreamableHTTPSessionManager(app=<low-level Server>,
       event_store=None, json_response=False, stateless=True)`.
       Note the kwarg is `stateless` (NOT `stateless_http`).
    2. Wrap `session_manager.run()` in a Starlette `lifespan` so the
       manager's background tasks start with the app and shut down
       cleanly on uvicorn termination.
    3. Mount a request handler at `/mcp` that delegates to
       `session_manager.handle_request(scope, receive, send)`.

    Factored out (rather than inlined into `run_streamable_http`) so
    Task 4 unit tests can construct the ASGI app without actually
    invoking `uvicorn.run` (which would block).
    """
    sdk_server = mcp_server.server
    session_manager = StreamableHTTPSessionManager(
        app=sdk_server,
        event_store=None,
        json_response=False,
        stateless=True,
    )

    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    return Starlette(
        routes=[Mount("/mcp", app=handle_streamable_http)],
        lifespan=lifespan,
    )


def run_streamable_http(
    mcp_server: MCPServer, *, host: str, port: int
) -> None:
    """Run the MCP server over streamable HTTP (service mode).

    Composes a Starlette ASGI app around `StreamableHTTPSessionManager`
    (see `_build_streamable_http_app`) and serves it via uvicorn.
    Stateless mode (DECIDE-H) — no session state on the server side;
    each request is independent.

    Synchronous wrapper around uvicorn.run (which manages its own event
    loop). Returns when the server shuts down (KeyboardInterrupt or
    uvicorn termination).
    """
    asgi_app = _build_streamable_http_app(mcp_server)
    uvicorn.run(
        asgi_app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
        # access_log goes to stderr by default — safe for the streamable
        # HTTP transport (no stdout-framing concern in HTTP mode).
    )
