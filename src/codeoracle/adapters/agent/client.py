"""HTTP client wrapping the agent process's POST /v1/query endpoint.

This adapter is the only place in the API process that imports httpx.
Per layer-purity rule 6 (spec §3.3), the API process is a thin
entrypoint — wrapping the external agent process's HTTP interface here
keeps `services/` and `routes/` ignorant of transport mechanics.
"""

import logging

import httpx
from opentelemetry import propagate
from pydantic import SecretStr

from codeoracle.adapters.agent.errors import (
    AgentResponseError,
    AgentUnreachableError,
)
from codeoracle.models.query import Query, QueryResult

logger = logging.getLogger(__name__)

_QUERY_PATH = "/v1/query"
_BODY_EXCERPT_BYTES = 512
_HTTP_OK_MIN = 200
_HTTP_REDIRECT_MIN = 300


class AgentClient:
    """Async HTTP client for the agent's POST /v1/query.

    One instance lives for the whole API process lifetime (constructed in
    `api/app.py` lifespan, stored on `app.state`, drained on shutdown).
    The class owns a single `httpx.AsyncClient`; per-request, it injects
    the W3C traceparent header (so the agent's FastAPI OTel instrumentor
    picks up the same trace) and the slice-scope shared-token header
    (only when configured — slice default omits it).
    """

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        shared_token: SecretStr | None,
    ) -> None:
        """Construct the AgentClient.

        Args: base_url: The agent's URL — `settings.api.agent_base_url`
                (default `http://127.0.0.1:8081` for single-host slice;
                override per Plan 10 Deviation 9 for multi-host).
            request_timeout_seconds: Wall-clock cap for the whole
                request (`settings.api.agent_request_timeout_seconds`,
                default 35.0). Connect-vs-read split stays inside
                httpx; this is the outer cap.
            shared_token: Optional per-process shared secret (SecretStr)
                for the slice+1 reactivation of agent auth. Held wrapped;
                unwrapped via get_secret_value() only at the header write.
                Slice default is `None` — no header sent.
        """
        self._base_url = base_url.rstrip("/")
        self._shared_token = shared_token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(request_timeout_seconds),
        )

    async def run(self, query: Query) -> QueryResult:
        """Send the query to the agent and parse the response.

        Returns: The parsed `QueryResult` (Plan 3 model with `extra="forbid"`).

        Raises: AgentUnreachableError: transport-layer failure (connect,
                DNS, TLS, total timeout). API routes map this to HTTP 503.
            AgentResponseError: non-2xx HTTP response. API routes map
                this to HTTP 502.
            RuntimeError: the client has been closed via `aclose()`.
                This is a programmer error (lifespan misuse); routes do
                NOT catch it.
        """
        headers: dict[str, str] = {}
        propagate.inject(headers)
        if self._shared_token is not None:
            headers["X-CodeOracle-Shared-Token"] = (
                self._shared_token.get_secret_value()
            )

        body = query.model_dump(mode="json")
        try:
            response = await self._client.post(
                _QUERY_PATH,
                json=body,
                headers=headers,
            )
        except httpx.RequestError as exc:
            # Transport-layer failure — connect refused, DNS, TLS,
            # total timeout. Anything that prevented us from getting a
            # response goes through here. `RequestError` is the parent
            # of `ConnectError`, `ReadTimeout`, `RemoteProtocolError`,
            # etc.
            logger.warning(
                "agent_unreachable",
                extra={"base_url": self._base_url, "error": str(exc)},
            )
            raise AgentUnreachableError(
                f"agent at {self._base_url} unreachable: {exc}"
            ) from exc

        if not (_HTTP_OK_MIN <= response.status_code < _HTTP_REDIRECT_MIN):
            body_excerpt = response.text[:_BODY_EXCERPT_BYTES]
            logger.warning(
                "agent_response_error",
                extra={
                    "status_code": response.status_code,
                    "base_url": self._base_url,
                },
            )
            raise AgentResponseError(
                status_code=response.status_code,
                body_excerpt=body_excerpt,
            )

        # Pydantic v2 model_validate raises ValidationError if the agent
        # sent an unrecognized shape — let it propagate (becomes HTTP 500
        # at the route layer; debuggable via structured log). This is
        # rare and indicates a Plan 3 / Plan 10 wire-contract drift, not
        # something the slice should silently absorb.
        return QueryResult.model_validate(response.json())

    async def aclose(self) -> None:
        """Drain the underlying httpx.AsyncClient. Idempotent (httpx's
        own aclose is idempotent — but documenting the contract here
        anchors the call sites in `api/app.py` lifespan).
        """
        await self._client.aclose()
