"""Pure-function pipeline for project-mode SCIP extraction.

The consumer's `handle()` method delegates to this function. Factoring it
out lets the function be unit-tested with mocked adapters (no Valkey, no
real subprocess).

Returns a per-file outcome status dict: {file_path: "success" | "failed"}.

Architectural invariants enforced here:
- One outcome row per file in `message.files` (invariant 2).
- Project-level failure → all-file failure (invariant 3).
- Per-file atomicity for the graph + outcome writes (invariant 5).
"""

import logging
import os
import pathlib
import tempfile

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser, ScipParseError
from codeoracle.core.exceptions import ScipExtractionError
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
    SnapshotFileOutcomeRow,
)
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage

_LOG = logging.getLogger(__name__)


async def extract_project_to_graph(
    *,
    message: ScipIndexerMessage,
    scip_client: ScipClient,
    parser: ScipIndexParser,
    scip_graph_repo: ScipGraphRepository,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
) -> dict[str, str]:
    """Run SCIP on the project, write per-file subgraphs + outcomes.

    The `session` parameter is the worker's outer session — it is COMMITTED
    incrementally per-file, then handed back. The function does NOT close it.
    """
    if not message.files:
        return {}

    # 1. Run scip-python + scip print. On failure, mark all files failed.
    try:
        with tempfile.TemporaryDirectory(prefix="scip-") as tmpdir:
            output_path = str(pathlib.Path(tmpdir) / "index.scip")
            await scip_client.index_project(
                project_dir=message.repo_root,
                # arbitrary; SCIP uses for symbol prefixing
                project_name="codeoracle",
                output_path=output_path,
            )
            scip_text = await scip_client.print_index(scip_file=output_path)
    except ScipExtractionError as e:
        _LOG.error(
            "scip extraction failed; marking all files failed", exc_info=e
        )
        return await _mark_all_failed(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"scip extraction: {str(e)[:512]}",
        )

    # 2. Parse output. Parse failure is project-wide.
    try:
        result = parser.parse(scip_text)
    except ScipParseError as e:
        _LOG.error(
            "scip output unparsable; marking all files failed", exc_info=e
        )
        return await _mark_all_failed(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"scip parse: {str(e)[:512]}",
        )

    # 3. NORMALIZE SCIP-emitted file paths to message.files entries, then
    #    group. SCIP-python emits file_paths relative to its project root;
    #    those may NOT be byte-identical to message.files entries (leading
    #    "./", different intermediate path prefix, abs path). If we skipped
    #    normalization, `defs_by_file.get(file_path, [])` would silently
    #    return [] for every file and write outcome=success rows with an
    #    empty graph — a true silent-failure mode.
    defs_by_file: dict[str, list] = {}
    for d in result.definitions:
        matched = _normalize_scip_path_to_message_file(
            d.file_path,
            message.files,
            message.repo_root,
        )
        if matched is not None:
            defs_by_file.setdefault(matched, []).append(d)
    refs_by_file: dict[str, list] = {}
    for r in result.references:
        matched = _normalize_scip_path_to_message_file(
            r.file_path,
            message.files,
            message.repo_root,
        )
        if matched is not None:
            refs_by_file.setdefault(matched, []).append(r)

    # 4. Loop files. For each file: one transaction.
    statuses: dict[str, str] = {}
    for file_path in message.files:
        defs = defs_by_file.get(file_path, [])
        refs = refs_by_file.get(file_path, [])
        try:
            await scip_graph_repo.replace_file_subgraph(
                snapshot_sha=message.snapshot_sha,
                file_path=file_path,
                definitions=defs,
                references=refs,
            )
            await outcome_repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=file_path,
                    extractor="scip",
                    outcome="success",
                    error_message=None,
                )
            )
            await session.commit()
            statuses[file_path] = "success"
        except Exception as e:
            # Per-file write failure (graph error, PG error, etc.) — log + mark
            # this file failed; continue with remaining files.
            _LOG.exception("per-file SCIP write failed for %s", file_path)
            await session.rollback()
            await outcome_repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=file_path,
                    extractor="scip",
                    outcome="failed",
                    error_message=f"per-file scip write: {str(e)[:512]}",
                )
            )
            await session.commit()
            statuses[file_path] = "failed"

    return statuses


async def _mark_all_failed(
    *,
    message: ScipIndexerMessage,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
    error_message: str,
) -> dict[str, str]:
    """Write `outcome=failed` rows for every file in `message.files`."""
    for file_path in message.files:
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=file_path,
                extractor="scip",
                outcome="failed",
                error_message=error_message,
            )
        )
    await session.commit()
    return dict.fromkeys(message.files, "failed")


def _normalize_scip_path_to_message_file(
    scip_path: str, message_files: list[str], repo_root: str
) -> str | None:
    """
    Map a SCIP-emitted file path to one of `message_files` (or None if no
    match).

    Why this exists: SCIP-python emits paths relative to whatever it considers
    the project root. `message.files` contains paths relative to `repo_root`.
    They are NOT guaranteed byte-identical. Without normalization, the
    `defs_by_file.get(file_path, [])` lookup at the per-file loop returns []
    for every file, silently dropping all symbols while the barrier closes
    with outcome=success — a true silent-failure mode.

    Match strategy (in order):
      1. Strip leading `repo_root/` (absolute-path emission).
      2. Strip leading `./`.
      3. Exact match in `message_files`? Return it.
      4. Suffix match: any `message_file` is the trailing component of
         `scip_path` (or vice versa)? Return that.
      5. Otherwise None — symbol is for a file outside the requested set
         (build artifact, third-party module SCIP traversed). Drop silently.
    """
    p = scip_path
    # Strip absolute repo_root prefix in either separator.
    for sep in (os.sep, "/"):
        prefix = repo_root.rstrip(sep) + sep
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    # Strip leading "./".
    if p.startswith("./"):
        p = p[2:]
    if p in message_files:
        return p
    # Suffix-match either direction.
    for mf in message_files:
        if p.endswith("/" + mf) or mf.endswith("/" + p):
            return mf
    return None
