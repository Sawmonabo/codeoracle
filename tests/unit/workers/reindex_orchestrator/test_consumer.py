"""
Plan 5 Task 13a: ReindexOrchestratorConsumer unit tests — mocked everything
below.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.reindex_orchestrator.consumer import (
    ReindexOrchestratorConsumer,
)
from codeoracle.workers.reindex_orchestrator.message import (
    ReindexRequestMessage,
)

pytestmark = pytest.mark.asyncio


async def test_handle_delegates_to_indexing_service_with_pre_allocated_sha():
    indexing = AsyncMock()
    consumer = ReindexOrchestratorConsumer(
        queue_client=AsyncMock(),
        retry_policy=RetryPolicy(),
        consumer_name="reindex_orchestrator-test",
        stop_event=asyncio.Event(),
        indexing_service=indexing,
    )
    msg = ReindexRequestMessage(
        batch_id="sha123",
        snapshot_sha="sha123",
        base_sha="base456",
    )

    await consumer.handle(msg)

    indexing.reindex.assert_awaited_once_with(
        base_sha="base456",
        pre_allocated_snapshot_sha="sha123",
    )


async def test_handle_passes_base_sha_none_when_unset():
    indexing = AsyncMock()
    consumer = ReindexOrchestratorConsumer(
        queue_client=AsyncMock(),
        retry_policy=RetryPolicy(),
        consumer_name="reindex_orchestrator-test",
        stop_event=asyncio.Event(),
        indexing_service=indexing,
    )
    msg = ReindexRequestMessage(batch_id="sha123", snapshot_sha="sha123")

    await consumer.handle(msg)

    indexing.reindex.assert_awaited_once_with(
        base_sha=None,
        pre_allocated_snapshot_sha="sha123",
    )


def test_message_rejects_extra_fields():
    """
    WorkerMessage's `extra='forbid'` applies — typos surface at validation.
    """
    with pytest.raises(ValidationError):
        ReindexRequestMessage.model_validate(
            {"batch_id": "x", "snapshot_sha": "x", "unknown_field": "boom"}
        )


def test_message_requires_snapshot_sha():
    with pytest.raises(ValidationError):
        ReindexRequestMessage.model_validate({"batch_id": "x"})
