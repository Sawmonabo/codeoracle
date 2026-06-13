"""OpengrepRunnerConsumer wiring."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_consumer_handle_runs_pipeline() -> None:
    runner = MagicMock(spec=OpengrepRunner)
    session = MagicMock()
    factory = MagicMock(return_value=_AsyncContext(session))
    queue_client = MagicMock()
    consumer = OpengrepRunnerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="opengrep-test",
        stop_event=asyncio.Event(),
        runner=runner,
        db_session_factory=factory,
    )
    msg = OpengrepRunnerMessage(
        batch_id="sha-cw",
        snapshot_sha="sha-cw",
        repo_root="/tmp/repo",
        file_path="a.py",
    )

    with patch(
        "codeoracle.workers.opengrep_runner.consumer."
        "extract_file_to_references",
        new=AsyncMock(),
    ) as task:
        await consumer.handle(msg)

    task.assert_awaited_once()
    assert task.await_args is not None
    assert task.await_args.kwargs["message"] == msg
    assert task.await_args.kwargs["runner"] == runner
