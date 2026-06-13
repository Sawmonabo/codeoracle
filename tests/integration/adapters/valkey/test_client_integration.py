"""Plan 5 Task 2: ValkeyQueueClient integration tests against real Valkey.

Marked `worker` so they're skipped in the fast unit lane. Uses
testcontainers' redis fixture (Valkey speaks the Redis protocol).
"""

import pytest

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig

pytestmark = pytest.mark.worker


# valkey_url and valkey_container fixtures are provided by
# tests/integration/conftest.py (Task 1 Step 7) — DO NOT redefine them
# here. Module-scoped duplicates would silently shadow the conftest copy
# and create a maintenance trap.


@pytest.fixture
async def queue_client(valkey_url: str):
    config = ValkeyConfig(
        url=valkey_url,
        pool_min=1,
        pool_max=10,
        block_ms_default=1000,
        max_in_flight_per_consumer=10,
        stream_maxlen=1000,
    )
    qc = ValkeyQueueClient(config)
    await qc.start()
    yield qc
    await qc.aclose()


async def test_round_trip_enqueue_read_ack(queue_client):
    """
    Full roundtrip: enqueue → read_pending returns it → ack removes from PEL.
    """
    msg_id = await queue_client.enqueue(
        "test.roundtrip", {"hello": "world", "n": 42}
    )

    msgs = await queue_client.read_pending(
        stream="test.roundtrip",
        group="test_group",
        consumer="test-consumer-1",
        count=10,
        block_ms=1000,
    )
    assert len(msgs) == 1
    m = msgs[0]
    assert m.id == msg_id
    assert m.payload == {"hello": "world", "n": 42}
    assert m.delivered_count >= 1

    await queue_client.ack("test.roundtrip", "test_group", m.id)

    # Reading again with ">" returns nothing — the message was acked
    msgs_after = await queue_client.read_pending(
        stream="test.roundtrip",
        group="test_group",
        consumer="test-consumer-1",
        count=10,
        block_ms=100,
    )
    assert msgs_after == []


async def test_two_consumers_in_same_group_load_balance(queue_client):
    """XREADGROUP load-balances across consumers in the same group."""
    for i in range(10):
        await queue_client.enqueue("test.lb", {"i": i})

    msgs_a = await queue_client.read_pending(
        stream="test.lb",
        group="g",
        consumer="a",
        count=5,
        block_ms=500,
    )
    msgs_b = await queue_client.read_pending(
        stream="test.lb",
        group="g",
        consumer="b",
        count=5,
        block_ms=500,
    )
    ids_a = {m.id for m in msgs_a}
    ids_b = {m.id for m in msgs_b}
    # Disjoint — Valkey gave different messages to each consumer
    assert ids_a.isdisjoint(ids_b)
    assert len(ids_a) + len(ids_b) == 10


async def test_dead_letter_writes_to_dlq_and_ack_clears_original(queue_client):
    """A dead-lettered message survives in the DLQ stream."""
    msg_id = await queue_client.enqueue("test.fail", {"will": "fail"})
    msgs = await queue_client.read_pending(
        stream="test.fail",
        group="g",
        consumer="c",
        count=10,
        block_ms=500,
    )
    assert len(msgs) == 1

    await queue_client.dead_letter(
        stream="test.fail",
        message_id=msg_id,
        payload={"will": "fail"},
        error="TerminalWorkerError: deliberate",
    )
    await queue_client.ack("test.fail", "g", msg_id)

    # DLQ stream now has 1 entry; read it via a fresh group on the DLQ
    dlq_msgs = await queue_client.read_pending(
        stream="test.fail.dlq",
        group="dlq_reader",
        consumer="r",
        count=10,
        block_ms=500,
    )
    assert len(dlq_msgs) == 1
    assert dlq_msgs[0].payload["original_id"] == msg_id
    assert "deliberate" in dlq_msgs[0].payload["error"]


async def test_consumer_group_bootstrap_is_idempotent(queue_client):
    """Calling read_pending twice with the same group doesn't error."""
    await queue_client.read_pending(
        stream="test.idem",
        group="g",
        consumer="c",
        count=1,
        block_ms=100,
    )
    # Second call — group already exists, must not raise
    await queue_client.read_pending(
        stream="test.idem",
        group="g",
        consumer="c",
        count=1,
        block_ms=100,
    )


async def test_reclaim_stale_advances_delivered_count(queue_client):
    """LOAD-BEARING reaper proof: a message read once (delivered_count=1) and
    left in the PEL (its consumer "crashed") is handed back by reclaim_stale
    with delivered_count=2 — the real XAUTOCLAIM delivery-counter bump that
    drives RetryPolicy toward dead-letter. Without it, ">" never re-reads the
    PEL and the count is frozen at 1 forever (the original marooning bug).
    """
    await queue_client.enqueue("test.reclaim", {"k": "v"})
    first = await queue_client.read_pending(
        stream="test.reclaim",
        group="g",
        consumer="crashed",
        count=10,
        block_ms=500,
    )
    assert len(first) == 1
    assert first[0].delivered_count == 1

    # "crashed" never ACKs — the entry stays in the PEL. A second consumer
    # reclaims it (min_idle_ms=0 → immediately eligible in-test).
    reclaimed = await queue_client.reclaim_stale(
        stream="test.reclaim",
        group="g",
        consumer="reaper",
        min_idle_ms=0,
        count=10,
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].id == first[0].id
    assert reclaimed[0].delivered_count == 2  # XAUTOCLAIM bumped it


async def test_reclaim_stale_skips_fresh_entries(queue_client):
    """The min-idle gate is what stops the reaper stealing a still-running
    handle: an entry younger than min_idle_ms is NOT reclaimed.
    """
    await queue_client.enqueue("test.reclaim_fresh", {"k": "v"})
    await queue_client.read_pending(
        stream="test.reclaim_fresh",
        group="g",
        consumer="c",
        count=10,
        block_ms=500,
    )
    # Just delivered — idle ≈ 0, far below the 1-hour gate.
    reclaimed = await queue_client.reclaim_stale(
        stream="test.reclaim_fresh",
        group="g",
        consumer="reaper",
        min_idle_ms=3_600_000,
        count=10,
    )
    assert reclaimed == []
