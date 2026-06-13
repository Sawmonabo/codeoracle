"""IndexingOrchestrator — fan out extraction jobs and wait on the barrier.

Each extractor declares whether it consumes per-file messages or one
project-mode message. The completion barrier counts only files routed to each
extractor, so code-only extractors do not wait forever on doc-only snapshots.
"""

import asyncio
import logging
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from codeoracle.core.extractors import (
    DEFAULT_EXTRACTOR_KINDS,
    INFO_BY_KIND,
    ExtractorInfo,
)
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)

logger = logging.getLogger(__name__)

_STREAM_BY_KIND: dict[str, str] = {
    kind: info.stream for kind, info in INFO_BY_KIND.items()
}


class QueueClientProtocol(Protocol):
    async def enqueue(
        self, stream: str, payload: dict[str, object]
    ) -> str: ...


class AsyncSessionFactoryProtocol(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[Any]: ...


class IndexingOrchestrator:
    _INFO_BY_KIND: dict[str, ExtractorInfo] = INFO_BY_KIND

    def __init__(
        self,
        *,
        queue_client: QueueClientProtocol,
        db_session_factory: AsyncSessionFactoryProtocol,
        extractor_kinds: Sequence[str] = DEFAULT_EXTRACTOR_KINDS,
        completion_poll_interval_seconds: float = 1.0,
        completion_timeout_seconds: float = 600.0,
        poll_interval_seconds: float | None = None,
        max_wait_seconds: float | None = None,
    ) -> None:
        """Construct the orchestrator.

        A fresh SnapshotFileOutcomeRepository is constructed per poll call.
        Holding one across the loop would leak the session and crash on
        connection-pool churn; the session must be scoped to one logical query,
        not the orchestrator's lifetime.
        """
        self._validate_kinds(extractor_kinds)
        self._queue = queue_client
        self._db = db_session_factory
        self._kinds = tuple(extractor_kinds)
        self._poll = (
            completion_poll_interval_seconds
            if poll_interval_seconds is None
            else poll_interval_seconds
        )
        self._timeout = (
            completion_timeout_seconds
            if max_wait_seconds is None
            else max_wait_seconds
        )

    async def fan_out_and_wait(
        self,
        *,
        snapshot_sha: str,
        repo_root: str,
        files: Sequence[str],
        extractor_kinds: Sequence[str] | None = None,
    ) -> dict[str, str]:
        """Enqueue routed extractor jobs and wait for completion.

        Returns a per-extractor outcome map:
        ``success`` means every routed file succeeded or no files routed;
        ``partial`` means some routed files failed; ``failed`` means all routed
        files failed; ``in_progress`` means the barrier has not closed.
        """
        kinds = (
            self._kinds if extractor_kinds is None else tuple(extractor_kinds)
        )
        self._validate_kinds(kinds)
        await self._enqueue_all(
            snapshot_sha=snapshot_sha,
            repo_root=repo_root,
            files=files,
            extractor_kinds=kinds,
        )
        return await self._wait_for_completion(
            snapshot_sha=snapshot_sha,
            files=files,
            kinds=kinds,
        )

    async def _enqueue_all(
        self,
        snapshot_sha: str,
        repo_root: str,
        files: Sequence[str],
        extractor_kinds: Sequence[str],
    ) -> dict[str, int]:
        kinds = self._validate_kinds(extractor_kinds)
        file_counts = self._expected_counts(files, kinds)

        for kind in kinds:
            info = INFO_BY_KIND[kind]
            routed_files = self._filter_files_for(files=files, kind=kind)
            if info.mode == "project":
                if routed_files:
                    await self._queue.enqueue(
                        info.stream,
                        {
                            "batch_id": snapshot_sha,
                            "snapshot_sha": snapshot_sha,
                            "files": routed_files,
                            "repo_root": repo_root,
                        },
                    )
                continue

            for file_path in routed_files:
                await self._queue.enqueue(
                    info.stream,
                    {
                        "batch_id": snapshot_sha,
                        "snapshot_sha": snapshot_sha,
                        "file_path": file_path,
                        "repo_root": repo_root,
                    },
                )

        logger.info(
            "orchestrator_enqueued",
            extra={
                "snapshot_sha": snapshot_sha,
                "n_files": len(files),
                "extractors": list(kinds),
                "file_counts_by_extractor": file_counts,
            },
        )
        return file_counts

    async def _wait_for_completion(
        self,
        *,
        snapshot_sha: str,
        files: Sequence[str],
        kinds: Sequence[str] | None = None,
    ) -> dict[str, str]:
        poll_kinds = self._kinds if kinds is None else tuple(kinds)
        elapsed = 0.0
        outcomes: dict[str, str] = {}
        while elapsed < self._timeout:
            outcomes = await self._collect_outcomes(
                snapshot_sha=snapshot_sha,
                files=files,
                extractor_kinds=poll_kinds,
            )
            if all(value != "in_progress" for value in outcomes.values()):
                return outcomes
            await asyncio.sleep(self._poll)
            elapsed += self._poll
        return outcomes or dict.fromkeys(poll_kinds, "in_progress")

    async def _collect_outcomes(
        self,
        snapshot_sha: str,
        files: Sequence[str],
        extractor_kinds: Sequence[str] | None = None,
    ) -> dict[str, str]:
        """Collect one barrier snapshot using one scoped DB session."""
        outcomes: dict[str, str] = {}
        poll_kinds = (
            self._kinds if extractor_kinds is None else tuple(extractor_kinds)
        )
        expected_by_kind = self._expected_counts(files, poll_kinds)

        async with self._db() as session:
            outcomes_repo = SnapshotFileOutcomeRepository(session)
            for kind in poll_kinds:
                expected_files = expected_by_kind[kind]
                if expected_files == 0:
                    outcomes[kind] = "success"
                    continue

                completed = await outcomes_repo.count_completed(
                    snapshot_sha=snapshot_sha,
                    extractor=kind,
                )
                if completed < expected_files:
                    outcomes[kind] = "in_progress"
                    continue

                failed = await outcomes_repo.count_failed(
                    snapshot_sha=snapshot_sha,
                    extractor=kind,
                )
                if failed == 0:
                    outcomes[kind] = "success"
                elif failed == expected_files:
                    outcomes[kind] = "failed"
                else:
                    outcomes[kind] = "partial"
        return outcomes

    async def _is_barrier_closed(
        self,
        *,
        snapshot_sha: str,
        files: Sequence[str],
        extractor_kinds: Sequence[str] | None = None,
    ) -> bool:
        poll_kinds = (
            self._kinds if extractor_kinds is None else tuple(extractor_kinds)
        )
        expected_by_kind = self._expected_counts(files, poll_kinds)
        async with self._db() as session:
            outcomes_repo = SnapshotFileOutcomeRepository(session)
            for kind, expected in expected_by_kind.items():
                if expected == 0:
                    continue
                completed = await outcomes_repo.count_completed(
                    snapshot_sha=snapshot_sha,
                    extractor=kind,
                )
                if completed < expected:
                    return False
        return True

    @classmethod
    def _validate_kinds(cls, kinds: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(kinds)
        unknown = [kind for kind in normalized if kind not in INFO_BY_KIND]
        if unknown:
            raise ValueError(
                f"IndexingOrchestrator: unknown extractor_kinds {unknown!r}."
            )
        return normalized

    @classmethod
    def _filter_files_for(
        cls,
        *,
        files: Sequence[str],
        kind: str,
    ) -> list[str]:
        cls._validate_kinds((kind,))
        accepted_extensions = INFO_BY_KIND[kind].accepted_extensions
        if accepted_extensions is None:
            return list(files)
        return [
            file_path
            for file_path in files
            if any(
                file_path.lower().endswith(extension)
                for extension in accepted_extensions
            )
        ]

    @classmethod
    def _expected_counts(
        cls,
        files: Sequence[str],
        kinds: Sequence[str],
    ) -> dict[str, int]:
        return {
            kind: len(cls._filter_files_for(files=files, kind=kind))
            for kind in cls._validate_kinds(kinds)
        }
