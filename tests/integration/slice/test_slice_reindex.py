"""Plan 13 reindex slice gate."""

import asyncio
import time

import httpx
import pytest

from codeoracle.core.extractors import DEFAULT_EXTRACTOR_KINDS
from codeoracle.models.snapshot import Snapshot, SnapshotStatus

_POLL_INTERVAL_SECONDS = 1.0
_POLL_BUDGET_SECONDS = 180.0


@pytest.mark.slice
async def test_reindex_endpoint_produces_new_snapshot(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    post_response = await api_client.post(
        "/v1/reindex", json={"base_sha": None}
    )
    assert post_response.status_code == 202, post_response.text
    enqueued = Snapshot.model_validate(post_response.json())
    assert enqueued.status in {
        SnapshotStatus.PENDING,
        SnapshotStatus.APPLYING,
        SnapshotStatus.APPLIED,
    }

    deadline = time.monotonic() + _POLL_BUDGET_SECONDS
    final: Snapshot | None = None
    while time.monotonic() < deadline:
        get_response = await api_client.get(f"/v1/reindex/{enqueued.sha}")
        assert get_response.status_code == 200, get_response.text
        current = Snapshot.model_validate(get_response.json())
        if current.status in {SnapshotStatus.APPLIED, SnapshotStatus.FAILED}:
            final = current
            break
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    assert final is not None, (
        f"reindex {enqueued.sha} did not finish within {_POLL_BUDGET_SECONDS}s"
    )
    assert final.status == SnapshotStatus.APPLIED, final.error
    assert set(final.extractor_results) == set(DEFAULT_EXTRACTOR_KINDS)
