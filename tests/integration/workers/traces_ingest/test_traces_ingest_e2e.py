"""Plan 7 Task 12 — end-to-end traces_ingest happy path.

testcontainers PG + respx-mocked Tempo. Spins up a TracesIngestConsumer,
serves three batches of mocked Tempo responses, advances the cursor
across ticks, asserts the spans landed in traces.spans, and tears down.
"""

import asyncio

import pytest
import respx

from codeoracle.adapters.traces.tempo_client import TempoClient
from codeoracle.core.config import TracesConfig
from codeoracle.core.exceptions import TerminalFetchError
from codeoracle.core.logging import get_logger
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import SpanFilter, TimestampCursor
from codeoracle.workers._base.cursor_store_pg import PGCursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer

pytestmark = [pytest.mark.traces, pytest.mark.integration]


# Static test data — three batches of growing trace IDs
_TRACE_IDS = [bytes.fromhex(f"{i:032x}") for i in range(1, 10)]


def _otlp_trace_payload(trace_id_hex: str) -> dict:
    """Tempo 2.6+ envelope shape (top-level key `resourceSpans`)."""
    return {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "api"},
                            }
                        ],
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "codeoracle.api",
                                "version": "0.1.0",
                            },
                            "spans": [
                                {
                                    "traceId": trace_id_hex,
                                    "spanId": f"{1:016x}",
                                    "parentSpanId": "",
                                    "name": "POST /v1/query",
                                    "kind": 2,
                                    "startTimeUnixNano": "1700000000000000000",
                                    "endTimeUnixNano": "1700000000010000000",
                                    "status": {"code": 1, "message": ""},
                                    "attributes": [
                                        {
                                            "key": "http.route",
                                            "value": {
                                                "stringValue": "/v1/query"
                                            },
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }


def _search_payload(trace_id_hexes: list[str]) -> dict:
    return {
        "traces": [
            {
                "traceID": h,
                "rootServiceName": "api",
                "rootTraceName": "POST /v1/query",
                "startTimeUnixNano": "1700000000000000000",
                "durationMs": 10,
            }
            for h in trace_id_hexes
        ],
        "metrics": {"inspectedTraces": len(trace_id_hexes)},
    }


@pytest.mark.asyncio
@respx.mock
async def test_consumer_ingests_three_batches_and_stops_on_signal(
    async_session_factory, async_engine
) -> None:
    tempo_base = "http://tempo.test:3200"

    # ----- Tempo mocks: three rounds, each with one trace -----
    round_1_hex = _TRACE_IDS[0].hex()
    round_2_hex = _TRACE_IDS[1].hex()
    round_3_hex = _TRACE_IDS[2].hex()

    # First search hits → round 1; next → round 2; etc.
    # We can't easily sequence respx responses without a side_effect; use a
    # counter-keyed lambda via respx.route.mock(side_effect=[...]).
    respx.get(f"{tempo_base}/api/search").mock(
        side_effect=[
            respx.MockResponse(json=_search_payload([round_1_hex])),
            respx.MockResponse(json=_search_payload([round_2_hex])),
            respx.MockResponse(json=_search_payload([round_3_hex])),
            # Subsequent ticks return empty so the cursor doesn't advance
            # further
            respx.MockResponse(
                json={"traces": [], "metrics": {"inspectedTraces": 0}}
            ),
        ]
    )

    for hex_id in (round_1_hex, round_2_hex, round_3_hex):
        respx.get(f"{tempo_base}/api/v2/traces/{hex_id}").respond(
            json=_otlp_trace_payload(hex_id)
        )

    # ----- Build the consumer with a fast tick interval -----
    cs = PGCursorStore(
        session_factory=async_session_factory, cursor_type=TimestampCursor
    )
    repo = TracesRepository(
        session_factory=async_session_factory, cursor_store=cs
    )
    tempo = TempoClient(base_url=tempo_base, timeout_seconds=2.0)
    logger = get_logger("traces_ingest_e2e")
    stop = asyncio.Event()

    consumer = TracesIngestConsumer(
        tempo_client=tempo,
        traces_repo=repo,
        config=TracesConfig(
            tempo_url=tempo_base,
            tick_interval_seconds=0.01,
            max_backoff_seconds=0.05,
            search_limit=50,
            cursor_overlap_seconds=0,
        ),
        cursor_store=cs,
        stop_event=stop,
        logger=logger,
        engine=async_engine,
    )

    async def stop_after_three_rounds():
        # Give the consumer enough time to complete 3 ticks
        await asyncio.sleep(0.15)
        stop.set()

    try:
        await asyncio.gather(consumer.run(), stop_after_three_rounds())
    finally:
        await tempo.aclose()

    # ----- Assert: 3 traces, each with 1 span, landed in traces.spans -----
    for trace_id in _TRACE_IDS[:3]:
        out = await repo.find_spans(SpanFilter(trace_id=trace_id))
        assert len(out) == 1, (
            f"expected 1 span for trace {trace_id.hex()}, got {len(out)}"
        )

    # ----- Assert: cursor advanced (non-None, > 0) -----
    final_cursor = await repo.get_cursor()
    assert final_cursor is not None
    assert final_cursor.last_ingested_unix_nano > 0


@pytest.mark.asyncio
@respx.mock
async def test_consumer_recovers_after_transient_failure(
    async_session_factory, async_engine
) -> None:
    """
    Sequence: search 503 (transient) → backoff → search 200 (good) → persist.
    """
    tempo_base = "http://tempo.test:3200"
    hex_id = _TRACE_IDS[0].hex()

    respx.get(f"{tempo_base}/api/search").mock(
        side_effect=[
            respx.MockResponse(status_code=503, text="busy"),
            respx.MockResponse(json=_search_payload([hex_id])),
            respx.MockResponse(
                json={"traces": [], "metrics": {"inspectedTraces": 0}}
            ),
        ]
    )
    respx.get(f"{tempo_base}/api/v2/traces/{hex_id}").respond(
        json=_otlp_trace_payload(hex_id)
    )

    cs = PGCursorStore(
        session_factory=async_session_factory, cursor_type=TimestampCursor
    )
    repo = TracesRepository(
        session_factory=async_session_factory, cursor_store=cs
    )
    tempo = TempoClient(base_url=tempo_base, timeout_seconds=2.0)
    logger = get_logger("traces_ingest_e2e")
    stop = asyncio.Event()

    consumer = TracesIngestConsumer(
        tempo_client=tempo,
        traces_repo=repo,
        config=TracesConfig(
            tempo_url=tempo_base,
            tick_interval_seconds=0.005,
            max_backoff_seconds=0.05,
            search_limit=50,
            cursor_overlap_seconds=0,
        ),
        cursor_store=cs,
        stop_event=stop,
        logger=logger,
        engine=async_engine,
    )

    async def stop_soon():
        await asyncio.sleep(0.2)
        stop.set()

    try:
        await asyncio.gather(consumer.run(), stop_soon())
    finally:
        await tempo.aclose()

    out = await repo.find_spans(SpanFilter(trace_id=_TRACE_IDS[0]))
    assert len(out) == 1


@pytest.mark.asyncio
@respx.mock
async def test_consumer_dies_on_terminal_error(
    async_session_factory, async_engine
) -> None:
    """4xx on search → TerminalFetchError → consumer.run() raises."""
    tempo_base = "http://tempo.test:3200"
    respx.get(f"{tempo_base}/api/search").respond(
        status_code=400, text="bad query"
    )

    cs = PGCursorStore(
        session_factory=async_session_factory, cursor_type=TimestampCursor
    )
    repo = TracesRepository(
        session_factory=async_session_factory, cursor_store=cs
    )
    tempo = TempoClient(base_url=tempo_base, timeout_seconds=2.0)
    logger = get_logger("traces_ingest_e2e")
    stop = asyncio.Event()

    consumer = TracesIngestConsumer(
        tempo_client=tempo,
        traces_repo=repo,
        config=TracesConfig(
            tempo_url=tempo_base,
            tick_interval_seconds=0.001,
            max_backoff_seconds=0.05,
        ),
        cursor_store=cs,
        stop_event=stop,
        logger=logger,
        engine=async_engine,
    )

    try:
        with pytest.raises(TerminalFetchError):
            await consumer.run()
    finally:
        await tempo.aclose()
