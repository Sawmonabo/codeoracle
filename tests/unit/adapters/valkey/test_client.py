"""Plan 5 Task 2: ValkeyQueueClient unit tests with mocked `valkey-py`.

Real Valkey integration lives in tests/integration/adapters/valkey/.
Here we assert SHAPE: what the client sends, what it returns, how it
handles dead-letter, that groups are bootstrapped exactly once.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import codeoracle.adapters.valkey.client as client_mod
from codeoracle.adapters.valkey.client import (
    ConsumedMessage,
    ValkeyQueueClient,
)
from codeoracle.adapters.valkey.config import ValkeyConfig


def _config() -> ValkeyConfig:
    return ValkeyConfig(
        url="valkey://localhost:6379/0",
        pool_min=1,
        pool_max=10,
        block_ms_default=1000,
        max_in_flight_per_consumer=10,
        stream_maxlen=1000,
    )


@pytest.fixture
def mock_valkey(monkeypatch):
    """Patch `valkey.asyncio.from_url` to return a MagicMock."""
    fake = MagicMock()
    fake.ping = AsyncMock(return_value=True)
    fake.xadd = AsyncMock(return_value=b"1716123456789-0")
    fake.xreadgroup = AsyncMock(return_value=[])
    fake.xack = AsyncMock(return_value=1)
    fake.xgroup_create = AsyncMock(return_value=True)
    fake.xpending_range = AsyncMock(return_value=[])
    fake.xautoclaim = AsyncMock(return_value=(b"0-0", [], []))
    fake.aclose = AsyncMock()

    monkeypatch.setattr(client_mod.valkey, "from_url", lambda *a, **kw: fake)
    return fake


async def test_start_pings_valkey(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    mock_valkey.ping.assert_awaited_once()


async def test_enqueue_serializes_payload_as_json(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    entry_id = await qc.enqueue(
        "indexing.joern", {"snapshot_sha": "abc1234", "file": "src/x.py"}
    )
    assert entry_id == "1716123456789-0"
    args = mock_valkey.xadd.call_args
    assert args.args[0] == "indexing.joern"
    # Body is canonicalized JSON
    body = args.args[1]["json"]
    assert body == '{"file":"src/x.py","snapshot_sha":"abc1234"}'


async def test_read_pending_decodes_messages(mock_valkey):
    mock_valkey.xreadgroup.return_value = [
        (
            b"indexing.joern",
            [
                (
                    b"1716123456789-0",
                    {b"json": b'{"snapshot_sha":"abc1234","file":"src/x.py"}'},
                )
            ],
        )
    ]
    mock_valkey.xpending_range.return_value = [{"times_delivered": 1}]

    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.read_pending(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        count=10,
        block_ms=1000,
    )
    assert len(msgs) == 1
    assert msgs[0] == ConsumedMessage(
        id="1716123456789-0",
        stream="indexing.joern",
        payload={"snapshot_sha": "abc1234", "file": "src/x.py"},
        delivered_count=1,
    )


async def test_read_pending_bootstraps_group_once(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.read_pending(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        count=1,
        block_ms=0,
    )
    await qc.read_pending(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        count=1,
        block_ms=0,
    )
    # xgroup_create called only on first read
    mock_valkey.xgroup_create.assert_awaited_once()


async def test_read_pending_ignores_busygroup_response_error(mock_valkey):
    mock_valkey.xgroup_create.side_effect = client_mod.valkey.ResponseError(
        "BUSYGROUP Consumer Group name already exists"
    )
    qc = ValkeyQueueClient(_config())
    await qc.start()
    # Should NOT raise — group already exists is fine
    msgs = await qc.read_pending(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        count=1,
        block_ms=0,
    )
    assert msgs == []


async def test_dead_letter_writes_to_dlq_stream(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.dead_letter(
        stream="indexing.joern",
        message_id="1716-0",
        payload={"snapshot_sha": "abc", "file": "src/x.py"},
        error="JoernExtractionError: syntax error",
    )
    # XADD called against indexing.joern.dlq
    dlq_call = mock_valkey.xadd.call_args
    assert dlq_call.args[0] == "indexing.joern.dlq"
    fields = dlq_call.args[1]
    assert fields["original_id"] == "1716-0"
    assert fields["original_stream"] == "indexing.joern"
    assert "JoernExtractionError" in fields["error"]


async def test_ack_calls_xack(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.ack("indexing.joern", "joern_indexer", "1716-0")
    mock_valkey.xack.assert_awaited_with(
        "indexing.joern", "joern_indexer", "1716-0"
    )


async def test_aclose_is_idempotent(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.aclose()
    await qc.aclose()  # second call is a no-op, shouldn't raise
    mock_valkey.aclose.assert_awaited_once()


async def test_methods_raise_before_start():
    qc = ValkeyQueueClient(_config())
    with pytest.raises(RuntimeError, match="not started"):
        await qc.enqueue("indexing.joern", {})


async def test_read_pending_dead_letters_unparsable_payload(mock_valkey):
    mock_valkey.xreadgroup.return_value = [
        (
            b"indexing.joern",
            [(b"1716-0", {b"json": b"{not json"})],
        )
    ]
    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.read_pending(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        count=1,
        block_ms=0,
    )
    # Unparsable message is dead-lettered and skipped
    assert msgs == []
    # DLQ XADD + ACK on original
    dlq_calls = [
        c
        for c in mock_valkey.xadd.call_args_list
        if c.args[0] == "indexing.joern.dlq"
    ]
    assert len(dlq_calls) == 1
    mock_valkey.xack.assert_awaited()


async def test_reclaim_stale_decodes_with_advanced_delivered_count(
    mock_valkey,
):
    # XAUTOCLAIM hands back one stale entry; XPENDING reports the delivery
    # counter XAUTOCLAIM just bumped.
    mock_valkey.xautoclaim.return_value = (
        b"0-0",
        [
            (
                b"1716-0",
                {b"json": b'{"snapshot_sha":"abc1234","file":"src/x.py"}'},
            )
        ],
        [],
    )
    mock_valkey.xpending_range.return_value = [{"times_delivered": 3}]

    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.reclaim_stale(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        min_idle_ms=120_000,
        count=1,
    )
    assert msgs == [
        ConsumedMessage(
            id="1716-0",
            stream="indexing.joern",
            payload={"snapshot_sha": "abc1234", "file": "src/x.py"},
            delivered_count=3,
        )
    ]
    # The min-idle gate is forwarded to XAUTOCLAIM.
    assert mock_valkey.xautoclaim.call_args.kwargs["min_idle_time"] == 120_000


async def test_reclaim_stale_dead_letters_unparsable_payload(mock_valkey):
    mock_valkey.xautoclaim.return_value = (
        b"0-0",
        [(b"1716-0", {b"json": b"{not json"})],
        [],
    )
    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.reclaim_stale(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        min_idle_ms=120_000,
        count=1,
    )
    # Shared _decode_entry path: a malformed reclaimed entry is DLQ'd + ACKed
    # + skipped, exactly like read_pending.
    assert msgs == []
    dlq_calls = [
        c
        for c in mock_valkey.xadd.call_args_list
        if c.args[0] == "indexing.joern.dlq"
    ]
    assert len(dlq_calls) == 1
    mock_valkey.xack.assert_awaited()
