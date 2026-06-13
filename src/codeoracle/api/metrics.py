"""Prometheus metrics for the API process."""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import suppress
from typing import Any, cast

from prometheus_client import REGISTRY, Gauge
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core import streams

logger = logging.getLogger(__name__)


class ConnectionPoolCollector(Collector):
    """On-scrape SQLAlchemy pool-saturation gauges for one API engine."""

    def __init__(self, engine: AsyncEngine, *, process: str) -> None:
        self._pool = engine.sync_engine.pool
        self._process = process

    def collect(self) -> Iterator[GaugeMetricFamily]:
        pool = cast(Any, self._pool)
        for name, doc, value in (
            (
                "codeoracle_db_pool_checked_out_connections",
                "Connections currently checked out of the pool.",
                pool.checkedout(),
            ),
            (
                "codeoracle_db_pool_checked_in_connections",
                "Idle connections currently available in the pool.",
                pool.checkedin(),
            ),
            (
                "codeoracle_db_pool_overflow_connections",
                "Overflow connections open beyond pool_size.",
                pool.overflow(),
            ),
        ):
            gauge = GaugeMetricFamily(name, doc, labels=["process"])
            gauge.add_metric([self._process], float(value))
            yield gauge


VALKEY_STREAM_LENGTH = Gauge(
    "codeoracle_valkey_stream_length",
    "Current entry count (XLEN) of a Valkey worker stream.",
    ["stream"],
)
VALKEY_PENDING_ENTRIES = Gauge(
    "codeoracle_valkey_pending_entries",
    "Size of a consumer group's pending-entries list (XPENDING).",
    ["stream", "group"],
)

_MONITORED_QUEUES: tuple[tuple[str, str], ...] = (
    (streams.STREAM_INDEXING_JOERN, streams.GROUP_JOERN_INDEXER),
    (streams.STREAM_INDEXING_SCIP, streams.GROUP_SCIP_INDEXER),
    (streams.STREAM_INDEXING_OPENGREP, streams.GROUP_OPENGREP_RUNNER),
    (streams.STREAM_INDEXING_EMBED, streams.GROUP_EMBED_INDEXER),
    (streams.STREAM_REINDEX_REQUESTS, streams.GROUP_REINDEX_ORCHESTRATOR),
)
_QUEUE_METRICS_REFRESH_SECONDS: float = 15.0


def register_api_metrics(
    engine: AsyncEngine,
    *,
    process: str,
) -> ConnectionPoolCollector:
    """Register process-local API metrics and return the pool collector."""

    collector = ConnectionPoolCollector(engine, process=process)
    REGISTRY.register(collector)
    return collector


def unregister_api_metrics(collector: ConnectionPoolCollector) -> None:
    """Unregister process-local API metrics."""

    with suppress(KeyError):
        REGISTRY.unregister(collector)


async def _refresh_queue_gauges(queue_client: ValkeyQueueClient) -> None:
    """Set every monitored queue's length and pending-count gauges."""

    for stream, group in _MONITORED_QUEUES:
        VALKEY_STREAM_LENGTH.labels(stream=stream).set(
            await queue_client.stream_length(stream)
        )
        VALKEY_PENDING_ENTRIES.labels(stream=stream, group=group).set(
            await queue_client.pending_count(stream, group)
        )


async def _run_queue_metrics_refresher(
    queue_client: ValkeyQueueClient,
) -> None:
    """Refresh queue gauges until cancelled."""

    while True:
        try:
            await _refresh_queue_gauges(queue_client)
        except Exception as exc:
            logger.warning(
                "queue_metrics_refresh_failed",
                extra={"error": str(exc)},
            )
        await asyncio.sleep(_QUEUE_METRICS_REFRESH_SECONDS)


def start_queue_metrics_refresher(
    queue_client: ValkeyQueueClient,
) -> asyncio.Task[None]:
    """Start the background queue-metrics refresher."""

    return asyncio.create_task(_run_queue_metrics_refresher(queue_client))


async def stop_queue_metrics_refresher(
    task: asyncio.Task[None] | None,
) -> None:
    """Cancel and drain the background queue-metrics refresher."""

    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
