"""Plan 7 Task 5 — TempoClient unit tests with respx-mocked HTTP.

These tests pin the wire-format adapter: OTLP nanos-as-strings,
hex-encoded IDs, integer-encoded kinds, KV-list attributes, scope
flattening. Each gotcha gets a regression test.
"""

import base64
from typing import Any

import httpx
import pytest
import respx

from codeoracle.adapters.traces.tempo_client import (
    TempoClient,
    TempoTerminalError,
    TempoTransientError,
    _decode_otlp_bytes,
)
from codeoracle.models.traces import Span

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def tempo_base_url() -> str:
    return "http://tempo.test:3200"


@pytest.fixture
async def client(tempo_base_url: str):
    """Build a TempoClient and tear it down after the test.

    Under `asyncio_mode = "auto"` (set in pyproject.toml by Plan 3),
    plain `@pytest.fixture` on an async generator IS the right shape;
    pytest-asyncio adapts it. The earlier sync-fixture form that used
    `asyncio.get_event_loop().run_until_complete(c.aclose())` was
    fragile — on Python 3.10+ the bare `get_event_loop()` outside a
    running loop emits DeprecationWarning, and on current Python versions
    it raises
    if no loop is set on the calling thread. Async-yield is cleaner.
    """
    c = TempoClient(base_url=tempo_base_url, timeout_seconds=2.0)
    try:
        yield c
    finally:
        await c.aclose()


def _otlp_span_payload(  # noqa: PLR0913
    *,
    trace_id_hex: str = "0102030405060708090a0b0c0d0e0f10",
    span_id_hex: str = "1112131415161718",
    parent_span_id_hex: str = "",
    name: str = "GET /v1/query",
    kind: int = 2,  # server
    start_ns: int = 1_700_000_000_000_000_000,
    end_ns: int = 1_700_000_000_050_000_000,
    status_code: int = 1,
    status_message: str = "",
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an OTLP-JSON span dict matching Tempo's wire format."""
    attrs: list[dict[str, Any]] = []
    for k, v in (attributes or {}).items():
        if isinstance(v, str):
            attrs.append({"key": k, "value": {"stringValue": v}})
        elif isinstance(v, bool):
            attrs.append({"key": k, "value": {"boolValue": v}})
        elif isinstance(v, int):
            attrs.append(
                {"key": k, "value": {"intValue": str(v)}}
            )  # OTLP intValue is string
        elif isinstance(v, float):
            attrs.append({"key": k, "value": {"doubleValue": v}})
    return {
        "traceId": trace_id_hex,
        "spanId": span_id_hex,
        "parentSpanId": parent_span_id_hex,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": status_code, "message": status_message},
        "attributes": attrs,
    }


def _otlp_trace_payload(
    spans: list[dict[str, Any]],
    *,
    service_name: str = "api",
    scope_name: str = "codeoracle.api",
    scope_version: str = "0.1.0",
) -> dict[str, Any]:
    """
    Build the full OTLP trace response shape Tempo 2.6+ returns at
    /api/v2/traces/{id}.

    Top-level key is `resourceSpans` (per Tempo 2.6 release notes; renamed
    from pre-2.6 `batches`). Tests that need to exercise the legacy `batches`
    shape (pre-2.6 compatibility) construct it manually — the adapter still
    accepts it as a fallback per `_unwrap_trace_payload` in tempo_client.py.
    """
    return {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": service_name},
                            },
                        ],
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": scope_name,
                                "version": scope_version,
                            },
                            "spans": spans,
                        }
                    ],
                }
            ]
        }
    }


# -----------------------------------------------------------------------------
# search_traces_in_window
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_search_traces_returns_summaries(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        json={
            "traces": [
                {
                    "traceID": "0102030405060708090a0b0c0d0e0f10",
                    "rootServiceName": "api",
                    "rootTraceName": "POST /v1/query",
                    "startTimeUnixNano": "1700000000000000000",
                    "durationMs": 50,
                },
                {
                    "traceID": "1112131415161718192021222324252600",
                    "rootServiceName": "worker",
                    "rootTraceName": "process_message",
                    "startTimeUnixNano": "1700000000500000000",
                    "durationMs": 120,
                },
            ],
            "metrics": {"inspectedTraces": 2},
        }
    )

    summaries = await client.search_traces_in_window(
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=1_700_000_001_000_000_000,
        limit=10,
    )
    assert len(summaries) == 2
    assert summaries[0].trace_id == bytes.fromhex(
        "0102030405060708090a0b0c0d0e0f10"
    )
    assert summaries[0].root_service_name == "api"
    assert (
        summaries[0].duration_nanos == 50_000_000
    )  # ms → ns conversion at adapter boundary


@pytest.mark.asyncio
@respx.mock
async def test_search_traces_handles_empty_result(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        json={"traces": [], "metrics": {"inspectedTraces": 0}}
    )
    out = await client.search_traces_in_window(
        start_unix_nano=1,
        end_unix_nano=2,
        limit=10,
    )
    assert out == []


@pytest.mark.asyncio
@respx.mock
async def test_search_traces_sends_correct_query_params(
    client: TempoClient, tempo_base_url: str
) -> None:
    route = respx.get(f"{tempo_base_url}/api/search").respond(
        json={"traces": [], "metrics": {}}
    )
    await client.search_traces_in_window(
        start_unix_nano=1_000_000_000_000_000_000,
        end_unix_nano=2_000_000_000_000_000_000,
        limit=50,
    )
    assert route.called
    request = route.calls[0].request
    params = dict(request.url.params)
    assert (
        params["start"] == "1000000000000000000"
    )  # nanos as string for precision
    assert params["end"] == "2000000000000000000"
    assert params["limit"] == "50"
    assert params["q"] == "{}"  # TraceQL match-all


# -----------------------------------------------------------------------------
# get_trace_by_id — wire-format gotchas
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_flattens_resource_scope_and_spans(
    client: TempoClient, tempo_base_url: str
) -> None:
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(
        spans=[
            _otlp_span_payload(span_id_hex="aaaaaaaaaaaaaaaa"),
            _otlp_span_payload(span_id_hex="bbbbbbbbbbbbbbbb"),
        ],
        service_name="api",
        scope_name="codeoracle.api",
        scope_version="0.2.0",
    )
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=payload
    )

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert len(spans) == 2
    for span in spans:
        assert isinstance(span, Span)
        assert span.service_name == "api"  # from resource.attributes
        assert span.scope_name == "codeoracle.api"  # from scope.name
        assert span.scope_version == "0.2.0"


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_otlp_kind_integer(
    client: TempoClient, tempo_base_url: str
) -> None:
    """
    OTLP kind: 1=internal, 2=server, 3=client, 4=producer, 5=consumer,
    0=unspecified.
    """
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(
        spans=[
            _otlp_span_payload(span_id_hex=f"{i:016x}", kind=k)
            for i, k in enumerate([0, 1, 2, 3, 4, 5])
        ]
    )
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=payload
    )

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    kinds = [s.kind for s in spans]
    assert kinds == [
        "unspecified",
        "internal",
        "server",
        "client",
        "producer",
        "consumer",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_otlp_status_code_integer(
    client: TempoClient, tempo_base_url: str
) -> None:
    """OTLP status code: 0=unset, 1=ok, 2=error."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(
        spans=[
            _otlp_span_payload(
                span_id_hex=f"{i:016x}",
                status_code=c,
                status_message=("err" if c == 2 else ""),
            )
            for i, c in enumerate([0, 1, 2])
        ]
    )
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=payload
    )

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    codes = [s.status_code for s in spans]
    assert codes == ["unset", "ok", "error"]
    # Spans with error code must carry status_message (Span model invariant)
    error_span = next(s for s in spans if s.status_code == "error")
    assert error_span.status_message == "err"


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_attribute_value_tagged_union(
    client: TempoClient, tempo_base_url: str
) -> None:
    """
    OTLP attributes: [{key, value:
    {stringValue|intValue|boolValue|doubleValue}}].
    The adapter must unwrap the tagged-union into native Python types."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(
        spans=[
            _otlp_span_payload(
                attributes={
                    "http.route": "/v1/query",
                    "http.status_code": 200,
                    "ok": True,
                    "duration_ratio": 0.95,
                },
            )
        ]
    )
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=payload
    )

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    attrs = spans[0].attributes
    assert attrs["http.route"] == "/v1/query"
    assert attrs["http.status_code"] == 200
    assert attrs["ok"] is True
    assert attrs["duration_ratio"] == pytest.approx(0.95)


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_parent_span_id_empty_string_becomes_none(
    client: TempoClient, tempo_base_url: str
) -> None:
    """OTLP encodes root spans with parentSpanId = "" (empty string).
    The adapter must convert this to Python None (matching Span model)."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(
        spans=[
            _otlp_span_payload(parent_span_id_hex=""),
        ]
    )
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=payload
    )
    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert spans[0].parent_span_id is None


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_nanos_string_decoded_to_int(
    client: TempoClient, tempo_base_url: str
) -> None:
    """OTLP wire format encodes 64-bit nanos as STRING for JS-safety;
    the adapter must convert to int."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(
        spans=[
            _otlp_span_payload(
                start_ns=1_700_000_000_000_000_000,
                end_ns=1_700_000_000_050_000_000,
            ),
        ]
    )
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=payload
    )
    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert isinstance(spans[0].start_unix_nano, int)
    assert spans[0].start_unix_nano == 1_700_000_000_000_000_000
    assert spans[0].end_unix_nano == 1_700_000_000_050_000_000


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_missing_service_name_defaults_to_unknown(
    client: TempoClient, tempo_base_url: str
) -> None:
    """If resource.attributes lacks `service.name` (operator misconfig),
    the adapter logs + tags the spans as service_name='unknown' rather
    than raising — keep ingest running, surface via logs."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},  # no service.name
                    "scopeSpans": [
                        {
                            "scope": {"name": "x", "version": "1"},
                            "spans": [_otlp_span_payload()],
                        }
                    ],
                }
            ]
        }
    }
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=payload
    )
    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert spans[0].service_name == "unknown"


# -----------------------------------------------------------------------------
# Error classification
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_5xx_raises_transient(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        status_code=503, text="upstream busy"
    )
    with pytest.raises(TempoTransientError, match="503"):
        await client.search_traces_in_window(
            start_unix_nano=1, end_unix_nano=2, limit=10
        )


@pytest.mark.asyncio
@respx.mock
async def test_429_raises_transient(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        status_code=429, text="rate limited"
    )
    with pytest.raises(TempoTransientError, match="429"):
        await client.search_traces_in_window(
            start_unix_nano=1, end_unix_nano=2, limit=10
        )


@pytest.mark.asyncio
@respx.mock
async def test_400_raises_terminal(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        status_code=400, text="bad query"
    )
    with pytest.raises(TempoTerminalError, match="400"):
        await client.search_traces_in_window(
            start_unix_nano=1, end_unix_nano=2, limit=10
        )


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_terminal(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        status_code=401, text="unauthorized"
    )
    with pytest.raises(TempoTerminalError, match="401"):
        await client.search_traces_in_window(
            start_unix_nano=1, end_unix_nano=2, limit=10
        )


@pytest.mark.asyncio
@respx.mock
async def test_404_on_get_trace_raises_terminal(
    client: TempoClient, tempo_base_url: str
) -> None:
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        status_code=404
    )
    with pytest.raises(TempoTerminalError, match="404"):
        await client.get_trace_by_id(bytes.fromhex(trace_id_hex))


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_raises_transient(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").mock(
        side_effect=httpx.ConnectError("dns error")
    )
    with pytest.raises(TempoTransientError):
        await client.search_traces_in_window(
            start_unix_nano=1, end_unix_nano=2, limit=10
        )


@pytest.mark.asyncio
@respx.mock
async def test_timeout_raises_transient(
    client: TempoClient, tempo_base_url: str
) -> None:
    respx.get(f"{tempo_base_url}/api/search").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(TempoTransientError):
        await client.search_traces_in_window(
            start_unix_nano=1, end_unix_nano=2, limit=10
        )


# -----------------------------------------------------------------------------
# OTLP byte-decoding (hex AND base64) — Tempo wire format varies by build
# -----------------------------------------------------------------------------


def test_decode_otlp_bytes_accepts_hex() -> None:
    """
    Hex encoding (Tempo < 2.6 OTLP-JSON marshaller and most production builds):
    32 hex chars decode to 16 raw bytes for trace_id; 16 hex chars to 8 bytes
    for span_id.
    """
    decoded_trace = _decode_otlp_bytes(
        "0102030405060708090a0b0c0d0e0f10", expected_len=16
    )
    assert decoded_trace == bytes(range(1, 17))
    assert len(decoded_trace) == 16

    decoded_span = _decode_otlp_bytes("a1a2a3a4a5a6a7a8", expected_len=8)
    assert decoded_span == bytes(
        [0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8]
    )
    assert len(decoded_span) == 8


def test_decode_otlp_bytes_accepts_base64() -> None:
    """Base64 encoding (Tempo 2.6+ when OTLP-JSON marshaller chooses base64):
    24 base64 chars decode to 16 raw bytes; 12 base64 chars to 8 bytes."""
    raw_trace = bytes(range(1, 17))
    b64_trace = base64.b64encode(raw_trace).decode("ascii")
    assert len(b64_trace) == 24
    assert _decode_otlp_bytes(b64_trace, expected_len=16) == raw_trace

    raw_span = bytes([0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8])
    b64_span = base64.b64encode(raw_span).decode("ascii")
    assert len(b64_span) == 12
    assert _decode_otlp_bytes(b64_span, expected_len=8) == raw_span


def test_decode_otlp_bytes_rejects_neither() -> None:
    """Garbage input (neither hex nor base64) must raise ValueError — silent
    fallback to corrupted bytes would let bad IDs into traces.spans."""
    with pytest.raises(ValueError, match="empty OTLP byte field"):
        _decode_otlp_bytes("", expected_len=16)
    with pytest.raises(ValueError, match="neither hex nor base64"):
        # Wrong length AND not valid hex characters past position 0
        _decode_otlp_bytes("not-hex-not-b64!", expected_len=16)
    with pytest.raises(ValueError, match="decoded to 3 bytes"):
        # Valid base64 but wrong decoded length
        _decode_otlp_bytes(
            "AAAA", expected_len=16
        )  # 4 b64 chars = 3 bytes ≠ 16


# -----------------------------------------------------------------------------
# Legacy `batches` envelope (Tempo < 2.6 wire-format fallback)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_legacy_batches_envelope(
    client: TempoClient, tempo_base_url: str
) -> None:
    """Tempo 2.6 renamed the top-level key from `batches` to `resourceSpans` to
    match the OTLP spec. The adapter keeps backward compatibility with pre-2.6
    deployments by accepting EITHER key. This test exercises the `batches` path
    — without it, the fallback in `parser` is untested dead code."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    legacy_payload = {
        "trace": {
            # Note: `batches`, NOT `resourceSpans` — pre-2.6 wire format.
            "batches": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "legacy-api"},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "legacy.scope",
                                "version": "0.1.0",
                            },
                            "spans": [
                                _otlp_span_payload(
                                    span_id_hex="aaaabbbbccccdddd"
                                )
                            ],
                        }
                    ],
                }
            ]
        }
    }
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(
        json=legacy_payload
    )

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert len(spans) == 1
    assert spans[0].service_name == "legacy-api"  # resource attr decoded
    assert spans[0].scope_name == "legacy.scope"
    assert spans[0].span_id.hex() == "aaaabbbbccccdddd"


# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_releases_httpx_client(tempo_base_url: str) -> None:
    c = TempoClient(base_url=tempo_base_url, timeout_seconds=2.0)
    assert not c._client.is_closed  # internal check
    await c.aclose()
    assert c._client.is_closed
