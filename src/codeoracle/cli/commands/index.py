"""`codeoracle index` — CLI for reindexing.

Three modes: codeoracle index --since SHA   # incremental from base SHA
  codeoracle index --full        # full reindex (base_sha=None)
  codeoracle index               # incremental from last APPLIED snapshot

Exit codes: 0 — snapshot APPLIED (all extractors success or partial)
  2 — snapshot FAILED (timeout, joern config error, etc.)
  3 — AdvisoryLockTimeoutError (another reindex in flight)
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import load_valkey_config
from codeoracle.core.config import get_settings
from codeoracle.core.exceptions import AdvisoryLockTimeoutError
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.models.snapshot import SnapshotStatus
from codeoracle.services.indexing.service import IndexingService

logger = logging.getLogger(__name__)


def index_cmd(
    base_sha: Annotated[
        str | None,
        typer.Option(
            "--since", help="Incremental reindex from this base SHA."
        ),
    ] = None,
    full: Annotated[
        bool,
        typer.Option("--full", help="Full reindex (overrides --since)."),
    ] = False,
    repo_root: Annotated[
        Path,
        typer.Option(
            "--repo-root",
            exists=True,
            file_okay=False,
            help="Path to the repo to reindex. Default: CWD.",
        ),
    ] = Path("."),
    wait_seconds: Annotated[
        float,
        typer.Option(
            "--wait",
            help=(
                "Seconds to wait for the advisory lock. Default 0 (fail-fast)."
            ),
        ),
    ] = 0.0,
) -> None:
    """Reindex the corpus into AGE."""
    asyncio.run(
        _run_index(
            base_sha=base_sha,
            full=full,
            repo_root=repo_root,
            wait_seconds=wait_seconds,
        )
    )


async def _run_index(
    *, base_sha: str | None, full: bool, repo_root: Path, wait_seconds: float
) -> None:
    # Plan 3 signature: make_async_engine() reads Settings internally;
    # make_async_session_factory(engine) binds the factory to that engine.
    engine = make_async_engine()
    session_factory = make_async_session_factory(engine)
    queue_client = ValkeyQueueClient(load_valkey_config())
    await queue_client.start()
    settings = get_settings()

    # If neither --full nor --since: default to latest APPLIED
    if not full and base_sha is None:
        async with session_factory() as session:
            repo = SnapshotRepository(session)
            latest = await repo.get_latest_applied()
        if latest is not None:
            base_sha = latest.sha
            typer.echo(
                f"Using base_sha from latest APPLIED snapshot: {base_sha}"
            )
        else:
            typer.echo("No prior APPLIED snapshot — performing full reindex.")
            full = True

    service = IndexingService(
        engine=engine,
        db_session_factory=session_factory,
        queue_client=queue_client,
        repo_root=str(repo_root),
        extractor_kinds=tuple(settings.indexing.extractor_kinds),
        advisory_lock_timeout_seconds=wait_seconds,
    )
    try:
        snap = await service.reindex(base_sha=None if full else base_sha)
    except AdvisoryLockTimeoutError as exc:
        typer.echo(f"Lock timeout: {exc}", err=True)
        await queue_client.aclose()
        await engine.dispose()
        sys.exit(3)

    await queue_client.aclose()
    await engine.dispose()

    if snap.status == SnapshotStatus.APPLIED:
        typer.echo(f"\nSnapshot {snap.sha} APPLIED.")
        for k, v in snap.extractor_results.items():
            typer.echo(f"  {k}: {v.value}")
        sys.exit(0)
    else:
        typer.echo(f"\nSnapshot {snap.sha} FAILED: {snap.error}", err=True)
        sys.exit(2)
