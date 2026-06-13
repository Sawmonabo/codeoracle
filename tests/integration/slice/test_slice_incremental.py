"""Plan 13 incremental reindex slice gates."""

import json
import time
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from codeoracle.models.snapshot import Snapshot
from tests.integration.slice._helpers import drive_reindex_to_applied

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROBE_FILE = _REPO_ROOT / "tests" / "fixtures" / "slice_incremental_probe.py"
_PERF_REPORT_PATH = _REPO_ROOT / "perf-report.json"


async def _timed_reindex(
    api_client: httpx.AsyncClient, *, base_sha: str | None
) -> tuple[Snapshot, float]:
    started = time.perf_counter()
    snapshot = await drive_reindex_to_applied(api_client, base_sha=base_sha)
    return snapshot, time.perf_counter() - started


async def _count_graph_symbols(
    db_conn: asyncpg.Connection, snapshot_sha: str
) -> int:
    rows = await db_conn.fetch(
        """
        SELECT * FROM cypher(
            'codeoracle',
            $$ MATCH (s:Symbol {snapshot_sha: $sha}) RETURN count(s) AS n $$,
            CAST($1 AS agtype)
        ) AS (n agtype)
        """,
        json.dumps({"sha": snapshot_sha}),
    )
    if not rows:
        return 0
    return int(str(rows[0]["n"]).strip('"'))


def _write_perf_report(payload: dict[str, Any]) -> None:
    _PERF_REPORT_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.slice
async def test_incremental_reindex_creates_new_snapshot_after_file_edit(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    base, _ = await _timed_reindex(api_client, base_sha=None)
    original = _PROBE_FILE.read_text(encoding="utf-8")

    try:
        _PROBE_FILE.write_text(
            original
            + "\n\ndef slice_incremental_live_edit_marker() -> str:\n"
            + '    return "slice-live-edit"\n',
            encoding="utf-8",
        )
        edited, _ = await _timed_reindex(api_client, base_sha=base.sha)
    finally:
        _PROBE_FILE.write_text(original, encoding="utf-8")

    assert edited.sha != base.sha
    assert edited.parent_sha == base.sha
    assert "tests/fixtures/slice_incremental_probe.py" in (
        edited.changed_files
    )


@pytest.mark.slice
async def test_incremental_reindex_records_changed_files_and_graph_state(
    api_client: httpx.AsyncClient,
    db_conn: asyncpg.Connection,
    clean_snapshot: None,
) -> None:
    base, _ = await _timed_reindex(api_client, base_sha=None)
    original = _PROBE_FILE.read_text(encoding="utf-8")

    try:
        _PROBE_FILE.write_text(
            original.replace(
                "slice_incremental_probe",
                "slice_incremental_probe_renamed",
            ),
            encoding="utf-8",
        )
        edited, _ = await _timed_reindex(api_client, base_sha=base.sha)
        after_symbols = await _count_graph_symbols(db_conn, edited.sha)
    finally:
        _PROBE_FILE.write_text(original, encoding="utf-8")

    assert "tests/fixtures/slice_incremental_probe.py" in (
        edited.changed_files
    )
    assert after_symbols > 0


@pytest.mark.slice
async def test_incremental_reindex_performance_report_is_measured(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    full, full_seconds = await _timed_reindex(api_client, base_sha=None)
    no_op, no_op_seconds = await _timed_reindex(api_client, base_sha=full.sha)
    original = _PROBE_FILE.read_text(encoding="utf-8")

    try:
        _PROBE_FILE.write_text(
            original + "\n# slice perf measurement edit\n",
            encoding="utf-8",
        )
        incremental, incremental_seconds = await _timed_reindex(
            api_client, base_sha=no_op.sha
        )
        query_started = time.perf_counter()
        query_response = await api_client.post(
            "/v1/query",
            json={
                "type": "explain_symbol",
                "symbol": "codeoracle.services.verifier.service."
                "VerifierService.verify",
                "corpus": "self",
                "snapshot_sha": incremental.sha,
            },
        )
        query_seconds = time.perf_counter() - query_started
    finally:
        _PROBE_FILE.write_text(original, encoding="utf-8")

    assert query_response.status_code == 200, query_response.text
    _write_perf_report(
        {
            "full_reindex_seconds": full_seconds,
            "incremental_reindex_seconds": incremental_seconds,
            "no_op_reindex_seconds": no_op_seconds,
            "query_seconds": query_seconds,
            "snapshots": {
                "full": full.sha,
                "no_op": no_op.sha,
                "incremental": incremental.sha,
            },
        }
    )
    assert _PERF_REPORT_PATH.is_file()
