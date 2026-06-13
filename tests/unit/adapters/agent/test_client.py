"""AgentClient unit tests — stub the agent HTTP endpoint with respx.

These tests pin the AgentClient wire contract: POST /v1/query body shape,
response parsing into QueryResult, and the error-classification boundary
(AgentUnreachableError vs AgentResponseError).
"""

import json

import httpx
import pytest
import respx
from opentelemetry.context import attach, detach
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)
from pydantic import SecretStr

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.agent.errors import (
    AgentResponseError,
    AgentUnreachableError,
)
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult, QueryStatus


@pytest.fixture
def query() -> Query:
    return Query(
        symbol="codeoracle.services.verifier.service.VerifierService.verify",
        snapshot_sha=None,
    )


@pytest.fixture
def query_result_payload() -> dict:
    """A PROVEN QueryResult serialised to JSON — the wire shape the agent
    returns. Built via the `QueryResult.proven` factory + `model_dump`
    so it is valid by construction against Plan 3's `extra="forbid"`
    contract (correct enum casing, >=1 citation on the Claim). The SHA
    lives in the citation handle (canonical QueryResult has no top-level
    snapshot_sha).
    """
    return QueryResult.proven(
        trace_id="00000000000000000000000000000001",
        claim=Claim(
            statement="It's defined in src/auth/hash.py:42 — see citation A.",
            citations=[
                Citation(
                    handle=EvidenceHandle(
                        type=HandleType.CODE_SPAN,
                        locator="file://src/codeoracle/core/security.py:1-10",
                        snapshot_sha="abc123def456",
                    ),
                    excerpt="def hash_password(raw: str) -> str:",
                    source_trust=9,
                )
            ],
            confidence=0.9,
        ),
        verdict=Verdict(
            status=VerdictStatus.PROVEN,
            reason="All citations resolved and match cached excerpts.",
            resolved_citations=[],
        ),
    ).model_dump(mode="json")


async def test_run_returns_query_result_on_2xx(query, query_result_payload):
    """Happy path: agent returns 200 with QueryResult JSON, AgentClient
    parses it back into the Pydantic model.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            result = await client.run(query)
        finally:
            await client.aclose()

    assert route.called
    assert isinstance(result, QueryResult)
    assert result.status == QueryStatus.COMPLETE
    assert result.claim is not None
    assert result.claim.citations[0].handle.snapshot_sha == "abc123def456"


async def test_run_sends_query_as_json_body(query, query_result_payload):
    """The POST body must be the Query model serialised — every field
    Plan 3's Query exposes, in the order Pydantic emits them. The agent
    side (Plan 10) validates with `extra="forbid"`, so an extra key would
    fail the request.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            await client.run(query)
        finally:
            await client.aclose()

    # respx records the sent request — assert body equality
    sent = route.calls[0].request
    assert sent.method == "POST"
    assert sent.url.path == "/v1/query"
    # Pydantic v2 model_dump_json round-trips through json.loads
    assert json.loads(sent.content.decode()) == query.model_dump(mode="json")


async def test_run_raises_agent_unreachable_on_connect_error(query):
    """Transport-layer failure (httpx.ConnectError, ReadTimeout, etc.)
    surfaces as AgentUnreachableError. The original exception is chained
    via __cause__ so observability tooling can still see the root cause.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(side_effect=httpx.ConnectError("nope"))
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentUnreachableError) as excinfo:
                await client.run(query)
        finally:
            await client.aclose()

    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


async def test_run_raises_agent_unreachable_on_total_timeout(query):
    """A request that times out (httpx.ReadTimeout / TimeoutException)
    is unreachable, not a bad gateway. The API maps this to HTTP 503 —
    the same as a connection refusal.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(side_effect=httpx.ReadTimeout("slow"))
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=0.1,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentUnreachableError):
                await client.run(query)
        finally:
            await client.aclose()


async def test_run_raises_agent_response_error_on_5xx(query):
    """Non-2xx response = the agent answered but with a failure. This is
    a bad-gateway situation, not unreachable. The API maps it to HTTP 502.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(
            return_value=httpx.Response(500, text="boom")
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentResponseError) as excinfo:
                await client.run(query)
        finally:
            await client.aclose()

    assert excinfo.value.status_code == 500
    assert "boom" in str(excinfo.value)


async def test_run_raises_agent_response_error_on_4xx(query):
    """4xx is also a response error — agent rejected the input. The API
    propagates 4xx as 502 (not as a passthrough) because the API has
    already validated the body; any 4xx the agent returns is a contract
    drift, not a client error.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(
            return_value=httpx.Response(422, json={"detail": "drift"})
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentResponseError) as excinfo:
                await client.run(query)
        finally:
            await client.aclose()

    assert excinfo.value.status_code == 422


async def test_run_propagates_traceparent_header(query, query_result_payload):
    """OTel traceparent must be injected into the outbound request via
    opentelemetry.propagate.inject(headers). Without the SDK installed
    in the test env the injector is a no-op — but the call site MUST be
    present so production runs (with init_telemetry()) emit it.

    To assert this without an SDK, we use the global propagator's API:
    the W3CTraceContextPropagator is registered by default, but it only
    writes a header if there is an active span context. We set one
    explicitly with NonRecordingSpan + a known SpanContext, then assert
    the header lands.
    """
    span_ctx = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0xFEDCBA0987654321,
        is_remote=False,
        trace_flags=TraceFlags(0x01),
    )
    ctx = set_span_in_context(NonRecordingSpan(span_ctx))

    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            tok = attach(ctx)
            try:
                await client.run(query)
            finally:
                detach(tok)
        finally:
            await client.aclose()

    sent = route.calls[0].request
    # W3C traceparent format: "00-<32hex trace_id>-<16hex span_id>-<2hex
    # flags>"
    traceparent = sent.headers.get("traceparent")
    assert traceparent is not None
    assert traceparent.startswith("00-")
    assert "1234567890abcdef1234567890abcdef" in traceparent.lower()


async def test_run_sends_shared_token_header_when_configured(
    query, query_result_payload
):
    """If `settings.agent.shared_token` is set (Plan 10 Deviation 9
    reactivation trigger), the AgentClient sends it as
    `X-CodeOracle-Shared-Token`. Slice default is None (no header sent).
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=SecretStr("secret-multi-host-token"),
        )
        try:
            await client.run(query)
        finally:
            await client.aclose()

    sent = route.calls[0].request
    assert (
        sent.headers.get("X-CodeOracle-Shared-Token")
        == "secret-multi-host-token"
    )


async def test_run_omits_shared_token_header_when_none(
    query, query_result_payload
):
    """Slice default: shared_token=None → no header (avoid sending an
    empty string which middleware might misread).
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            await client.run(query)
        finally:
            await client.aclose()

    sent = route.calls[0].request
    assert "X-CodeOracle-Shared-Token" not in sent.headers


async def test_aclose_is_idempotent():
    """Calling aclose() twice must not raise — the lifespan shutdown
    might be invoked from multiple paths (KeyboardInterrupt + normal
    exit) and we cannot guarantee single-shot semantics from the caller.
    """
    client = AgentClient(
        base_url="http://agent.test:8081",
        request_timeout_seconds=5.0,
        shared_token=None,
    )
    await client.aclose()
    await client.aclose()  # must not raise


async def test_run_after_aclose_raises_runtime_error(query):
    """Using a closed client must fail loudly — silently re-opening would
    mask a lifespan bug. httpx.AsyncClient already raises RuntimeError
    when used after aclose(); AgentClient surfaces this as RuntimeError
    (NOT AgentUnreachableError — this is a programmer error, not a
    transport failure).
    """
    client = AgentClient(
        base_url="http://agent.test:8081",
        request_timeout_seconds=5.0,
        shared_token=None,
    )
    await client.aclose()
    with pytest.raises(RuntimeError):
        await client.run(query)
