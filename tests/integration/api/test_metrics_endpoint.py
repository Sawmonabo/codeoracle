"""/metrics endpoint exposes Prometheus counters.

Spec §8.5 — Plan 1A's prometheus scrape job pings /metrics; Plan 11
ships the endpoint mounted via prometheus_client.make_asgi_app().
"""

import httpx
import pytest

from codeoracle.api.metrics import _refresh_queue_gauges
from codeoracle.core import streams


@pytest.mark.integration
async def test_metrics_endpoint_returns_text(api_app):
    """/metrics returns text/plain; version=0.0.4 (Prometheus exposition
    format) with at least one default counter line.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    # Process-level counters that prometheus_client emits by default
    # (process_start_time_seconds, process_cpu_seconds_total, etc.)
    assert "process_" in r.text
    # NFR D0-3 — the connection-pool saturation gauges registered for the API
    # engine in the lifespan are exposed alongside the default counters.
    assert "codeoracle_db_pool_checked_out_connections" in r.text
    assert 'process="api"' in r.text


@pytest.mark.integration
async def test_metrics_endpoint_exposes_queue_gauges(api_app):
    """NFR D4-4 — the Valkey queue-depth + pending-backlog gauges appear at
    /metrics after a refresh.

    The gauges are populated by a background refresher (async XLEN/XPENDING
    can't ride D0-3's sync on-scrape collector), so we drive one deterministic
    pass directly rather than racing the lifespan's first tick. The refresh
    runs against the live Valkey testcontainer with empty streams/groups (no
    worker ran), so it exercises the missing-stream→0 (XLEN) and NOGROUP→0
    (XPENDING) paths and every gauge resolves to 0 without error.
    """
    await _refresh_queue_gauges(api_app.state.resources.valkey_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    # Both gauge families are exposed, labelled per stream / per (stream,
    # group).
    assert "codeoracle_valkey_stream_length" in r.text
    assert "codeoracle_valkey_pending_entries" in r.text
    assert f'stream="{streams.STREAM_INDEXING_JOERN}"' in r.text
    assert f'group="{streams.GROUP_REINDEX_ORCHESTRATOR}"' in r.text
    # The refresh must be side-effect-free: `pending_count` maps NOGROUP to 0
    # rather than bootstrapping the group (which `_ensure_group` would record
    # in
    # `_ensured_groups`). No read/reclaim ran in this test, so an empty
    # bootstrap
    # set proves the metrics scrape created no consumer group as a side effect.
    assert not api_app.state.resources.valkey_client._ensured_groups
