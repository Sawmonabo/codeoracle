"""
Plan 5 Task 12: JoernIndexerConsumer unit tests — mocked everything below it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.adapters.valkey.client import ConsumedMessage
from codeoracle.core.exceptions import JoernExtractionError
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.joern_indexer.consumer import JoernIndexerConsumer


class _FakeQueue:
    """Minimal in-memory stand-in (same shape as Task 4's helper)."""

    def __init__(self, scripted: list[list[ConsumedMessage]]) -> None:
        self._s = scripted
        self.acks: list[tuple[str, str, str]] = []
        self.dlqs: list[tuple[str, str, str]] = []
        self.calls = 0

    async def read_pending(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[ConsumedMessage]:
        _ = (stream, group, consumer, count, block_ms)
        if self.calls < len(self._s):
            out = self._s[self.calls]
            self.calls += 1
            return out
        await asyncio.sleep(0.01)
        return []

    async def reclaim_stale(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int,
    ) -> list[ConsumedMessage]:
        _ = (stream, group, consumer, min_idle_ms, count)
        return []

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        self.acks.append((stream, group, message_id))

    async def dead_letter(
        self,
        *,
        stream: str,
        message_id: str,
        payload: dict[str, object],
        error: str,
    ) -> None:
        _ = payload
        self.dlqs.append((stream, message_id, error))


def _msg(file="src/x.py"):
    return ConsumedMessage(
        id="1-0",
        stream="indexing.joern",
        payload={
            "batch_id": "abc1234",
            "snapshot_sha": "abc1234",
            "file_path": file,
            "repo_root": "/tmp",
        },
        delivered_count=1,
    )


@pytest.fixture
def consumer_setup(tmp_path):
    """
    Returns (consumer, queue, joern_mock, parser_mock, session_factory_mock).
    """
    queue = _FakeQueue([[_msg()]])
    joern = MagicMock()
    parser = MagicMock()
    db = MagicMock()

    consumer = JoernIndexerConsumer(
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=3),
        consumer_name="t1",
        stop_event=asyncio.Event(),
        joern_client=joern,
        parser=parser,
        db_session_factory=db,
    )
    return consumer, queue, joern, parser, db


async def test_happy_path_invokes_joern_and_acks(consumer_setup):
    consumer, queue, joern, parser, db = consumer_setup

    # Joern returns a mock export; parser returns 1 cypher stmt
    joern.extract.return_value = MagicMock(
        export_dir=MagicMock(),
        file_path="/tmp/src/x.py",
    )
    parser.to_cypher.return_value = ["CREATE (:File {snapshot_sha:'abc1234'})"]

    # Mock the async session context — extract_file_to_graph uses
    # `async with db_session_factory() as session:` twice (once for
    # graph + file_outcome write, once isn't triggered on success).
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = AsyncMock()
    session_ctx.__aenter__.return_value.commit = AsyncMock()
    db.return_value = session_ctx

    # Patch GraphRepository + SnapshotFileOutcomeRepository constructors
    with (
        patch(
            "codeoracle.workers.joern_indexer.tasks.GraphRepository"
        ) as graph_repo_cls,
        patch(
            "codeoracle.workers.joern_indexer.tasks.SnapshotFileOutcomeRepository"
        ) as file_outcomes_cls,
    ):
        graph_repo_cls.return_value.replace_file_subgraph = AsyncMock()
        file_outcomes_cls.return_value.upsert = AsyncMock()

        task = asyncio.create_task(consumer.run())
        for _ in range(500):
            if queue.acks:
                break
            await asyncio.sleep(0.01)
        consumer._stop.set()
        await task

    joern.extract.assert_called_once()
    parser.to_cypher.assert_called_once()
    assert len(queue.acks) == 1


async def test_joern_extraction_failure_dead_letters(consumer_setup):
    consumer, queue, joern, _parser, db = consumer_setup
    joern.extract.side_effect = JoernExtractionError("syntax error")

    # File-outcome upsert still happens in the except block
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = AsyncMock()
    session_ctx.__aenter__.return_value.commit = AsyncMock()
    db.return_value = session_ctx

    with patch(
        "codeoracle.workers.joern_indexer.tasks.SnapshotFileOutcomeRepository"
    ) as file_outcomes_cls:
        file_outcomes_cls.return_value.upsert = AsyncMock()

        task = asyncio.create_task(consumer.run())
        for _ in range(500):
            if queue.dlqs:
                break
            await asyncio.sleep(0.01)
        consumer._stop.set()
        await task

    # 1 DLQ entry; the message was acked off the original stream
    assert len(queue.dlqs) == 1
    assert "Terminal failure" in queue.dlqs[0][2]
    # File-outcome row was written with outcome=failed
    file_outcomes_cls.return_value.upsert.assert_awaited()
    failed_call = file_outcomes_cls.return_value.upsert.await_args
    assert failed_call is not None
    assert failed_call.kwargs["outcome"] == "failed"
