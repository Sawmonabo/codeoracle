"""Plan 5 Task 4: WorkerConsumer unit tests."""

import asyncio

from codeoracle.adapters.valkey.client import ConsumedMessage
from codeoracle.core.exceptions import (
    TerminalWorkerError,
    TransientWorkerError,
)
from codeoracle.workers._base.consumer import WorkerConsumer
from codeoracle.workers._base.message import RetryPolicy, WorkerMessage


class _TestMessage(WorkerMessage):
    n: int


class _RecordingConsumer(WorkerConsumer[_TestMessage]):
    """Concrete subclass for testing — records every handle() invocation."""

    stream = "test.stream"
    group = "test_group"
    message_type = _TestMessage
    block_ms = 100  # short, so tests don't hang
    count_per_poll = 5

    def __init__(self, behavior, **kwargs):
        super().__init__(**kwargs)
        self._behavior = behavior
        self.handled: list[_TestMessage] = []

    async def handle(self, message: _TestMessage) -> None:
        self.handled.append(message)
        if callable(self._behavior):
            outcome = self._behavior(message)
            if isinstance(outcome, Exception):
                raise outcome


class _FakeQueue:
    """In-memory stand-in for ValkeyQueueClient."""

    def __init__(
        self,
        scripted: list[list[ConsumedMessage]],
        reclaimed: list[list[ConsumedMessage]] | None = None,
    ) -> None:
        self._scripted = scripted  # one list per read_pending call
        self._reclaimed = reclaimed or []  # one list per reclaim_stale call
        self.acks: list[tuple[str, str, str]] = []
        self.dlqs: list[tuple[str, str, dict, str]] = []
        self.calls = 0
        self.reclaim_calls = 0

    async def read_pending(self, **_):
        if self.calls < len(self._scripted):
            out = self._scripted[self.calls]
            self.calls += 1
            return out
        # After the script is exhausted, return [] forever so the loop
        # idles until stop_event is set.
        await asyncio.sleep(0.01)
        return []

    async def reclaim_stale(self, **_):
        if self.reclaim_calls < len(self._reclaimed):
            out = self._reclaimed[self.reclaim_calls]
            self.reclaim_calls += 1
            return out
        return []

    async def ack(self, stream, group, message_id):
        self.acks.append((stream, group, message_id))

    async def dead_letter(self, stream, message_id, payload, error):
        self.dlqs.append((stream, message_id, payload, error))


async def _run_until_processed(consumer, queue, expected_processed):
    """Run consumer until `expected_processed` ACKs+DLQs have happened."""
    task = asyncio.create_task(consumer.run())
    for _ in range(500):
        if len(queue.acks) + len(queue.dlqs) >= expected_processed:
            break
        await asyncio.sleep(0.01)
    consumer._stop.set()
    await task


async def test_success_path_acks():
    queue = _FakeQueue(
        [
            [
                ConsumedMessage(
                    id="1-0",
                    stream="test.stream",
                    payload={"batch_id": "b1", "n": 7},
                    delivered_count=1,
                )
            ]
        ]
    )
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert len(consumer.handled) == 1
    assert consumer.handled[0].n == 7
    assert queue.acks == [("test.stream", "test_group", "1-0")]
    assert queue.dlqs == []


async def test_terminal_error_dead_letters_immediately():
    queue = _FakeQueue(
        [
            [
                ConsumedMessage(
                    id="1-0",
                    stream="test.stream",
                    payload={"batch_id": "b1", "n": 1},
                    delivered_count=1,
                )
            ]
        ]
    )
    consumer = _RecordingConsumer(
        behavior=lambda m: TerminalWorkerError("nope"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert queue.dlqs
    assert "Terminal failure" in queue.dlqs[0][3]
    # Dead-letter ACKs the original too
    assert queue.acks == [("test.stream", "test_group", "1-0")]


async def test_transient_error_within_budget_leaves_in_pel():
    queue = _FakeQueue(
        [
            [
                ConsumedMessage(
                    id="1-0",
                    stream="test.stream",
                    payload={"batch_id": "b1", "n": 1},
                    delivered_count=2,
                )
            ]
        ]
    )
    consumer = _RecordingConsumer(
        behavior=lambda m: TransientWorkerError("temp"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    # Wait briefly then stop — the consumer should NOT ack or DLQ
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.2)
    consumer._stop.set()
    await task
    assert queue.acks == []
    assert queue.dlqs == []


async def test_transient_error_beyond_budget_dead_letters():
    queue = _FakeQueue(
        [
            [
                ConsumedMessage(
                    id="1-0",
                    stream="test.stream",
                    payload={"batch_id": "b1", "n": 1},
                    delivered_count=5,
                )
            ]
        ]
    )
    consumer = _RecordingConsumer(
        behavior=lambda m: TransientWorkerError("still failing"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert queue.dlqs
    assert "Retry budget exhausted" in queue.dlqs[0][3]


async def test_reclaimed_message_climbs_to_dead_letter():
    """LOAD-BEARING reaper proof at the consumer level: a transiently-failing
    message handed back by reclaim_stale with a climbing delivered_count is
    retried while in budget and dead-lettered once it hits max_attempts — the
    path that is UNREACHABLE without reclaim (">" never re-reads the PEL, so
    delivered_count would freeze at 1 and the message would maroon forever).
    """
    mid = "1716-0"

    def at(delivered_count):
        return [
            ConsumedMessage(
                id=mid,
                stream="test.stream",
                payload={"batch_id": "b1", "n": 1},
                delivered_count=delivered_count,
            )
        ]

    # read_pending returns nothing; the message only ever arrives via reclaim,
    # delivered_count climbing 3 → 4 → 5 (= max_attempts) across polls.
    queue = _FakeQueue(scripted=[], reclaimed=[at(3), at(4), at(5)])
    consumer = _RecordingConsumer(
        behavior=lambda m: TransientWorkerError("still failing"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
        reclaim_min_idle_ms=1,  # enable reclaim
    )
    await _run_until_processed(consumer, queue, 1)  # the eventual DLQ
    # dc=3 and dc=4 stayed in PEL (no ACK, no DLQ); dc=5 dead-letters.
    assert len(queue.dlqs) == 1
    assert "Retry budget exhausted" in queue.dlqs[0][3]
    # Dead-letter ACKs the original off the producer stream.
    assert queue.acks == [("test.stream", "test_group", mid)]
    # Handler ran once per reclaim cycle.
    assert len(consumer.handled) == 3


async def test_reclaim_disabled_skips_reclaim_stale():
    """A consumer with reclaim_min_idle_ms=None (the reindex orchestrator's
    posture) never calls reclaim_stale — its crash-recovery is elsewhere.
    """
    queue = _FakeQueue(
        scripted=[],
        reclaimed=[
            [
                ConsumedMessage(
                    id="1716-0",
                    stream="test.stream",
                    payload={"batch_id": "b1", "n": 1},
                    delivered_count=9,
                )
            ]
        ],
    )
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
        # reclaim_min_idle_ms omitted → None → reclaim disabled
    )
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.2)
    consumer._stop.set()
    await task
    # reclaim_stale never invoked; the scripted reclaim entry is ignored.
    assert queue.reclaim_calls == 0
    assert consumer.handled == []


async def test_schema_validation_failure_dead_letters():
    queue = _FakeQueue(
        [
            [
                ConsumedMessage(
                    id="1-0",
                    stream="test.stream",
                    # `n` should be int — pass a non-coercible value
                    payload={"batch_id": "b1", "n": "not an int"},
                    delivered_count=1,
                )
            ]
        ]
    )
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert queue.dlqs
    assert "Schema validation" in queue.dlqs[0][3]
    # Handler never invoked
    assert consumer.handled == []


async def test_unexpected_exception_classified_transient():
    queue = _FakeQueue(
        [
            [
                ConsumedMessage(
                    id="1-0",
                    stream="test.stream",
                    payload={"batch_id": "b1", "n": 1},
                    delivered_count=1,
                )
            ]
        ]
    )
    consumer = _RecordingConsumer(
        behavior=lambda m: ValueError("uncategorized"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.2)
    consumer._stop.set()
    await task
    # First-attempt uncategorized error: transient (no ACK, no DLQ yet)
    assert queue.acks == []
    assert queue.dlqs == []


async def test_stop_event_exits_loop_cleanly():
    queue = _FakeQueue([])  # no messages
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    consumer._stop.set()  # set before run starts
    await consumer.run()  # returns immediately
