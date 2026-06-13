# tests/integration/slice/_helpers.py
"""Assertion + driver utilities shared across the slice integration suite.

Three responsibilities:
1. `assert_query_results_equal_modulo_trace_id` — pins §8.5 gate 4
   (byte-equal QueryResult across MCP + HTTP transports modulo trace_id).
2. `extract_handle_types` — small accessor for citation handle-type sets
   used by gate 1 (≥ 2 handle types per PROVEN) and gate 9 (no
   TRACE_SPAN / DB_RECORD).
3. `drive_reindex_to_applied` — POST /v1/reindex then poll GET
   /v1/reindex/{sha} until status is terminal (APPLIED or FAILED).
   Amended 2026-05-26 per the Plan 11 sync→async pivot (POST returns
   202 + Snapshot(PENDING); polling is the wait surface). DECIDE-V
   pins the helper here so every reindex caller in Plan 13 shares the
   same polling contract (gate 5 + gates 6-8 incremental + the gate-1
   / gate-2 / gate-4 `_ensure_indexed` setup hook in Task 3).

Lives in `_helpers.py` (leading underscore) so pytest treats it as a
private module, not a test file.
"""

import asyncio
import json
import time

import httpx

from codeoracle.models.query import QueryResult
from codeoracle.models.snapshot import Snapshot, SnapshotStatus

_TRACE_ID_SENTINEL = "<TRACE_ID_REWRITTEN_FOR_PARITY>"

# Reindex polling budget (matches spec §8.5 cold-3K-LOC p95 target).
# The full reindex of src/codeoracle/ inside the compose stack takes
# ~60-90s wall-clock against the 4-extractor pipeline (joern + scip +
# opengrep + embed); the budget includes the queue-XADD → consumer-XREADGROUP
# round-trip plus the indexing time itself. Tightening this below
# the spec target would surface as a flaky test on cold CI runners; the
# §8.5 perf gate emits the actual observation to perf-report.json.
_REINDEX_POLL_INTERVAL_SECONDS = 1.0
_REINDEX_POLL_BUDGET_SECONDS = 180.0


def assert_query_results_equal_modulo_trace_id(
    *, api_result: QueryResult, mcp_result: QueryResult
) -> None:
    """Assert two QueryResult instances are byte-equal modulo trace_id.

    Both results are serialised via model_dump(mode='json'),
    trace_id is rewritten to a fixed sentinel on both sides, and the
    resulting dicts are compared via canonical JSON (sorted keys, no
    whitespace).

    Raises AssertionError with a unified-diff on mismatch.
    """
    api_dict = api_result.model_dump(mode="json")
    mcp_dict = mcp_result.model_dump(mode="json")
    api_dict["trace_id"] = _TRACE_ID_SENTINEL
    mcp_dict["trace_id"] = _TRACE_ID_SENTINEL

    api_json = json.dumps(api_dict, sort_keys=True, separators=(",", ":"))
    mcp_json = json.dumps(mcp_dict, sort_keys=True, separators=(",", ":"))

    if api_json != mcp_json:
        # Surface a diff that names the differing field for fast debug.
        for key in sorted(set(api_dict) | set(mcp_dict)):
            if api_dict.get(key) != mcp_dict.get(key):
                api_value = json.dumps(api_dict.get(key), sort_keys=True)
                mcp_value = json.dumps(mcp_dict.get(key), sort_keys=True)
                msg = (
                    f"§8.5 gate 4: QueryResult divergence at field {key!r}\n"
                    f"  api:  {api_value[:300]}\n"
                    f"  mcp:  {mcp_value[:300]}\n"
                )
                raise AssertionError(msg)
        # Fallback if structural sets matched but JSON didn't (shouldn't
        # happen).
        raise AssertionError("QueryResult JSON serialisations diverge.")


def extract_handle_types(result: QueryResult) -> set[str]:
    """Return the set of handle-type names across all citations.

    `result.claim` is non-None only when `verdict.status == "PROVEN"` or
    "REFUTED"; for UNKNOWN there are no citations to extract from.
    """
    if result.claim is None:
        return set()
    return {c.handle.type for c in result.claim.citations}


async def drive_reindex_to_applied(
    api_client: httpx.AsyncClient, *, base_sha: str | None
) -> Snapshot:
    """POST /v1/reindex then poll GET /v1/reindex/{sha} until terminal.

    Amended 2026-05-26 per Plan 11 sync→async pivot:
    - POST returns 202 + Snapshot(status=PENDING|APPLYING)
    - GET polls until status is APPLIED (success) or FAILED (raise)
    - Budget is `_REINDEX_POLL_BUDGET_SECONDS` (180s, spec §8.5 cold p95)
    - Interval is `_REINDEX_POLL_INTERVAL_SECONDS` (1.0s)

    The producer (POST) is idempotent on retry per Plan 11 invariant 4
    (post-amendment): duplicate POST against the same effective tree
    returns the existing PENDING/APPLYING/APPLIED row instead of
    XADDing a second enqueue. Callers that wish to drive a fresh
    reindex MUST mutate the tree first (or rely on `clean_snapshot`).

    Raises: AssertionError: budget exceeded before terminal status reached.
        AssertionError: terminal status is FAILED (carries error message).
    """
    post_response = await api_client.post(
        "/v1/reindex", json={"base_sha": base_sha}
    )
    assert post_response.status_code == 202, (
        "POST /v1/reindex expected 202, got "
        f"{post_response.status_code} {post_response.text}"
    )
    enqueued = Snapshot.model_validate(post_response.json())
    assert enqueued.status in {
        SnapshotStatus.PENDING,
        SnapshotStatus.APPLYING,
        SnapshotStatus.APPLIED,
    }, (
        f"POST /v1/reindex returned unexpected status "
        f"{enqueued.status!r} (expected PENDING|APPLYING|APPLIED)"
    )
    snapshot_sha = enqueued.sha

    deadline = time.monotonic() + _REINDEX_POLL_BUDGET_SECONDS
    final: Snapshot | None = None
    while time.monotonic() < deadline:
        get_response = await api_client.get(f"/v1/reindex/{snapshot_sha}")
        assert get_response.status_code == 200, (
            f"GET /v1/reindex/{snapshot_sha} expected 200, got "
            f"{get_response.status_code} {get_response.text}"
        )
        current = Snapshot.model_validate(get_response.json())
        if current.status in {SnapshotStatus.APPLIED, SnapshotStatus.FAILED}:
            final = current
            break
        await asyncio.sleep(_REINDEX_POLL_INTERVAL_SECONDS)

    assert final is not None, (
        f"Reindex {snapshot_sha} did not reach terminal status within "
        f"{_REINDEX_POLL_BUDGET_SECONDS}s budget"
    )
    if final.status == SnapshotStatus.FAILED:
        raise AssertionError(
            f"Reindex {snapshot_sha} terminated FAILED: {final.error!r}"
        )
    return final
