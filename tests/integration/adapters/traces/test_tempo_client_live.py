"""Plan 7 Task 6 — live Tempo smoke tests.

Skipped by default. Run with:
  uv run pytest tests/integration/adapters/traces/test_tempo_client_live.py
  --run-live-tempo

Env vars consumed: CODEORACLE_TRACES__TEMPO_URL              (required; e.g.,
  http://localhost:3200)
  CODEORACLE_TRACES__TEMPO_TIMEOUT_SECONDS  (optional; default 5.0 — matches
  the TracesConfig.tempo_timeout_seconds env
                                     cascade so the same variable controls
                                     both the live test and the wired consumer)
"""

import asyncio
import json
import os
import secrets
import time

import httpx
import pytest

from codeoracle.adapters.traces.tempo_client import (
    TempoClient,
    TempoTerminalError,
)

pytestmark = pytest.mark.live_tempo


@pytest.fixture
def tempo_url() -> str:
    url = os.environ.get("CODEORACLE_TRACES__TEMPO_URL")
    if not url:
        pytest.skip(
            "CODEORACLE_TRACES__TEMPO_URL not set "
            "(start `docker compose -f infra/compose/docker-compose.yml "
            "up tempo`)"
        )
    return url


@pytest.fixture
async def live_client(tempo_url: str):
    timeout = float(
        os.environ.get("CODEORACLE_TRACES__TEMPO_TIMEOUT_SECONDS", "5.0")
    )
    c = TempoClient(base_url=tempo_url, timeout_seconds=timeout)
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_tempo_ready_endpoint_responds(tempo_url: str) -> None:
    """Tempo's /ready should return 200 when the binary is up. This is a
    bare-HTTP probe — not via TempoClient — so we can sanity-check the
    deployment is alive before exercising the adapter."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{tempo_url}/ready")
        assert r.status_code == 200, (
            f"Tempo not ready at {tempo_url}: {r.text}"
        )


@pytest.mark.asyncio
async def test_search_returns_parseable_response(
    live_client: TempoClient,
) -> None:
    """Even on a fresh Tempo with zero traces, the search response must be
    JSON-parseable and have the `traces` field (possibly empty)."""
    summaries = await live_client.search_traces_in_window(
        start_unix_nano=1,
        # ~2255 — within BIGINT range; "match any"
        end_unix_nano=9_000_000_000_000_000_000,
        limit=10,
    )
    assert isinstance(summaries, list)


@pytest.mark.asyncio
async def test_get_nonexistent_trace_raises_terminal(
    live_client: TempoClient,
) -> None:
    """A randomly-generated trace ID that Tempo doesn't know about must
    surface as TempoTerminalError (404)."""
    bogus_trace = (
        b"\xde\xad\xbe\xef" * 4
    )  # 16 bytes — valid shape, no real trace
    with pytest.raises(TempoTerminalError, match="404"):
        await live_client.get_trace_by_id(bogus_trace)


@pytest.mark.asyncio
async def test_push_then_fetch_round_trip(
    live_client: TempoClient, tempo_url: str
) -> None:
    """The wire-format-drift canary: push a known trace via OTLP HTTP, wait
    for the ingester batch, then fetch it back and assert structure.

    This is the ONLY test in the suite that exercises real Tempo wire format
    end-to-end. The respx-mocked unit tests can drift silently if Tempo
    changes its envelope (e.g., the 2.6 `Batches → ResourceSpans` rename);
    this test catches that. If Tempo upgrades break this test, the adapter's
    envelope parser is the load-bearing site to update.

    Tempo's ingest endpoint is `/v1/traces` (OTLP HTTP). The test pushes to
    the OTLP-HTTP host port and queries on 3200. Against the canonical stack
    (`infra/compose/docker-compose.yml`, owned by Plan 1A) Tempo's query port
    is host 3200; its OTLP-HTTP port may be remapped (Plan 1A already remaps
    OTLP-gRPC to host 14317 to avoid colliding with the OTel Collector), so
    set `CODEORACLE_TRACES__TEMPO_OTLP_URL` to the OTLP-HTTP host base when it
    isn't
    `:4318` — the harness defaults to `tempo_url` with `:3200`→`:4318` only as
    a fallback. Tempo batches ingested spans before they're queryable — a
    5-15 second wait is typical; we poll up to 30s.
    """
    # Distinct trace per run — no cross-test interference.
    trace_id_bytes = secrets.token_bytes(16)
    span_id_bytes = secrets.token_bytes(8)
    trace_id_hex = trace_id_bytes.hex()
    span_id_hex = span_id_bytes.hex()
    start_nano = time.time_ns()
    end_nano = start_nano + 50_000_000  # +50ms

    # Derive the OTLP-HTTP base URL from the query URL by swapping the port.
    # Tempo's docker dev config maps 3200 -> 3200 (query) and 4318 -> 4318
    # (OTLP-HTTP ingest). Operators using a different host MUST set
    # CODEORACLE_TRACES__TEMPO_OTLP_URL to override.
    otlp_base = os.environ.get(
        "CODEORACLE_TRACES__TEMPO_OTLP_URL",
        tempo_url.replace(":3200", ":4318"),
    )

    otlp_payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "test-round-trip"},
                        }
                    ],
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "codeoracle.test",
                            "version": "0.1.0",
                        },
                        "spans": [
                            {
                                "traceId": trace_id_hex,
                                "spanId": span_id_hex,
                                "name": "round-trip-probe",
                                "kind": 2,  # server
                                "startTimeUnixNano": str(start_nano),
                                "endTimeUnixNano": str(end_nano),
                                "status": {"code": 1},  # ok
                            }
                        ],
                    }
                ],
            }
        ],
    }

    async with httpx.AsyncClient(timeout=10.0) as push_client:
        push_resp = await push_client.post(
            f"{otlp_base}/v1/traces",
            content=json.dumps(otlp_payload),
            headers={"Content-Type": "application/json"},
        )
        assert push_resp.status_code in (200, 202), (
            f"OTLP ingest rejected: {push_resp.status_code} {push_resp.text!r}"
        )

    # Poll for trace availability — Tempo's single-binary dev config batches
    # spans for several seconds before they're queryable.
    deadline = time.monotonic() + 30.0
    spans = None
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            spans = await live_client.get_trace_by_id(trace_id_bytes)
            if spans:
                break
        except TempoTerminalError as e:
            last_err = e  # 404 — keep polling
        await asyncio.sleep(1.0)

    assert spans, (
        f"trace {trace_id_hex} never became queryable within 30s "
        f"(last error: {last_err!r})"
    )

    # The structural assertions catch wire-format drift end-to-end.
    span = spans[0]
    assert span.trace_id == trace_id_bytes, "trace_id round-trip failed"
    assert span.span_id == span_id_bytes, "span_id round-trip failed"
    assert span.service_name == "test-round-trip"
    assert span.operation_name == "round-trip-probe"
    assert span.kind == "server"
    assert span.start_unix_nano == start_nano
    assert span.end_unix_nano == end_nano
    assert span.status_code == "ok"
