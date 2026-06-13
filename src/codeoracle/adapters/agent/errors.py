"""AgentClient error types.

The two leaves below are the entire taxonomy the API-side code matches on.
Anything else that escapes AgentClient (e.g. RuntimeError when the client
is used after aclose()) is a programmer error and is intentionally NOT
caught by the routes — those bubble as HTTP 500.
"""

from codeoracle.core.exceptions import CodeOracleError


class AgentClientError(CodeOracleError):
    """Base for all AgentClient-raised errors. Do not raise this directly;
    use one of the two concrete subclasses so routes can branch on the
    transport-vs-response distinction.
    """


class AgentUnreachableError(AgentClientError):
    """The agent could not be reached: transport failure (connect refused,
    DNS, TLS) or total timeout. The route layer maps this to HTTP 503
    Service Unavailable. The underlying httpx exception is chained via
    `__cause__` so OTel + structlog still surface the root cause.
    """


class AgentResponseError(AgentClientError):
    """The agent responded but with a non-2xx status. The route layer maps
    this to HTTP 502 Bad Gateway — the request reached the agent but the
    agent's reply violated the contract.

    Attributes: status_code: HTTP status the agent returned (non-2xx).
        body_excerpt: First 512 bytes of the response body, decoded as
            utf-8 with errors='replace'. Logged but never returned to the
            HTTP client — leaking agent-internals to users is a Phase 7
            concern (auth), not a slice concern.
    """

    def __init__(self, *, status_code: int, body_excerpt: str) -> None:
        self.status_code = status_code
        self.body_excerpt = body_excerpt
        super().__init__(
            f"agent returned HTTP {status_code}: {body_excerpt!r}"
        )
