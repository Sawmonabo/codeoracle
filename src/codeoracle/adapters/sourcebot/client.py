"""SourcebotClient — async HTTP wrapper for Sourcebot's GET /api/source.

Lifecycle: the client holds one httpx.AsyncClient for its entire lifetime.
Callers MUST `await client.aclose()` on shutdown. The composition root
(workers/_base/wiring.py and api/deps.py) registers an aclose-on-stop callback.

The class has exactly one public method: `fetch_span(path, line_range, ref)`.
A `repo` argument is intentionally absent: the client is constructed
with a single `default_repo` (per Invariant 6); multi-repo dispatch lives
above this layer, in composition root. Adding a `repo` kwarg here would
re-open that decision in every caller.
"""

import httpx
from pydantic import ValidationError

from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse
from codeoracle.core.exceptions import (
    SourcebotTerminalError,
    SourcebotTransientError,
)

_HTTP_BAD_REQUEST = 400
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500
_HTTP_SERVER_ERROR_MAX = 600
_BODY_EXCERPT_CHARS = 200


class SourcebotClient:
    """Async client for Sourcebot's HTTP source-fetch API.

    Single endpoint wrapped: GET /api/source?repo=<repo>&path=<path>&ref=<ref>
            Authorization: Bearer <api_key>
            → 200 validated against SourcebotSourceResponse: {"source": "<full
            file>"} → 4xx terminal (path/ref not found, auth fail)
            → 5xx / 429 transient

    Every successful response body is validated against
    `SourcebotSourceResponse` (extra=forbid) so schema drift surfaces as
    `SourcebotTerminalError` on every call (closes D-8.4).

    Line-range slicing is client-side — the endpoint has no offset/limit
    on the HTTP API (only the MCP-server tool variant does, which we do
    not use here).
    """

    def __init__(self, config: SourcebotConfig) -> None:
        # Construct the underlying httpx client with the auth header
        # pre-baked so callers don't risk forgetting it.
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Accept": "application/json",
            },
        )

    async def fetch_span(
        self,
        *,
        path: str,
        line_range: tuple[int, int],
        ref: str,
    ) -> str:
        """Fetch lines [line_start, line_end] (1-indexed, inclusive) of `path`
        at git ref `ref` in the client's configured default_repo.

        Returns the joined-with-newline content of those lines. If the file
        is shorter than line_end, the slice is clamped to the file's length
        (matches Python's list-slicing semantics). If line_start is past
        EOF, returns "".

        Raises:
            ValueError: line_range is malformed (start < 1, end < start, etc.)
            SourcebotTerminalError: 4xx response, non-JSON body, or response
                that fails SourcebotSourceResponse contract validation
                (missing `source` field, extra unexpected fields, wrong type).
            SourcebotTransientError: 5xx / 429, timeout, or connect error.
        """
        line_start, line_end = line_range
        if line_start < 1:
            raise ValueError(f"line_start must be >= 1, got {line_start}")
        if line_end < line_start:
            raise ValueError(
                f"line_end ({line_end}) must be >= line_start ({line_start})"
            )
        if not path:
            raise ValueError("path must be non-empty")
        if not ref:
            raise ValueError("ref must be non-empty")

        payload = await self._get_source(path=path, ref=ref)
        source = payload.source

        lines = source.splitlines()
        # Convert 1-indexed inclusive → 0-indexed half-open.
        start_idx = line_start - 1
        end_idx = line_end  # half-open upper bound; Python clamps if > len
        return "\n".join(lines[start_idx:end_idx])

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool. Idempotent."""
        await self._client.aclose()

    # ----- Internal -----

    async def _get_source(
        self, *, path: str, ref: str
    ) -> SourcebotSourceResponse:
        """
        Call GET /api/source. Returns the validated contract model or raises.

        The response body is validated against `SourcebotSourceResponse` —
        any drift in the wire shape (missing `source`, extra fields, wrong
        type) surfaces as `SourcebotTerminalError` (closes D-8.4).
        """
        params = {
            "repo": self._config.default_repo,
            "path": path,
            "ref": ref,
        }
        try:
            response = await self._client.get("/api/source", params=params)
        except httpx.TimeoutException as e:
            raise SourcebotTransientError(
                "timeout calling Sourcebot /api/source "
                f"(path={path!r} ref={ref!r}): {e!r}"
            ) from e
        except httpx.ConnectError as e:
            raise SourcebotTransientError(
                "connect error calling Sourcebot /api/source "
                f"(path={path!r} ref={ref!r}): {e!r}"
            ) from e
        except httpx.HTTPError as e:
            # Catches NetworkError + any other httpx error not enumerated above
            raise SourcebotTransientError(
                "httpx error calling Sourcebot /api/source "
                f"(path={path!r} ref={ref!r}): {e!r}"
            ) from e

        if (
            response.status_code == _HTTP_TOO_MANY_REQUESTS
            or _HTTP_SERVER_ERROR
            <= response.status_code
            < _HTTP_SERVER_ERROR_MAX
        ):
            raise SourcebotTransientError(
                f"Sourcebot /api/source returned {response.status_code} "
                f"(path={path!r} ref={ref!r}): "
                f"{response.text[:_BODY_EXCERPT_CHARS]}"
            )
        if _HTTP_BAD_REQUEST <= response.status_code < _HTTP_SERVER_ERROR:
            raise SourcebotTerminalError(
                f"Sourcebot /api/source returned {response.status_code} "
                f"(path={path!r} ref={ref!r}): "
                f"{response.text[:_BODY_EXCERPT_CHARS]}"
            )
        try:
            raw = response.json()
        except ValueError as e:
            raise SourcebotTerminalError(
                "Sourcebot /api/source returned non-JSON "
                f"(path={path!r} ref={ref!r}): "
                f"{response.text[:_BODY_EXCERPT_CHARS]}"
            ) from e
        try:
            return SourcebotSourceResponse.model_validate(raw)
        except ValidationError as e:
            raise SourcebotTerminalError(
                f"Sourcebot /api/source response failed contract validation "
                f"(path={path!r} ref={ref!r}): {e}"
            ) from e
