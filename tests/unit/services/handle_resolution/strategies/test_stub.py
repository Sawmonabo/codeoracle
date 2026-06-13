"""Plan 8 Task 12 — StubResolver tests.

Pins:
  1. resolve() ALWAYS raises UnsupportedHandleTypeError.
  2. The error message names the configured HandleType.
  3. The stub does NOT branch on the actual handle.type — its sole job
     is to fail loudly with a typed exception. (Mismatched handle types
     are caught by the dispatcher; the stub doesn't double-check.)
"""

import pytest

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.stub import StubResolver


@pytest.mark.asyncio
async def test_resolve_always_raises_unsupported() -> None:
    stub = StubResolver(handle_type=HandleType.TRACE_SPAN)
    handle = EvidenceHandle(
        type=HandleType.TRACE_SPAN,
        locator="trace://abc123",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await stub.resolve(handle)
    assert "trace_span" in str(exc.value).lower()
    assert "slice" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_resolve_for_db_record_names_db_record() -> None:
    stub = StubResolver(handle_type=HandleType.DB_RECORD)
    handle = EvidenceHandle(
        type=HandleType.DB_RECORD,
        locator="db://schema.table:42",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await stub.resolve(handle)
    assert "db_record" in str(exc.value).lower()
