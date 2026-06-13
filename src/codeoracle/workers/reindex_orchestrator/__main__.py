"""Reindex orchestrator worker entrypoint —
`python -m codeoracle.workers.reindex_orchestrator`."""

import asyncio

from codeoracle.core.config import get_settings
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.wiring import (
    build_worker_context,
    teardown_worker_context,
)
from codeoracle.workers.reindex_orchestrator.consumer import (
    ReindexOrchestratorConsumer,
)


async def amain() -> None:
    ctx = await build_worker_context(
        consumer_name_prefix="reindex_orchestrator"
    )
    # `get_settings()` is the module-scoped singleton already used by
    # build_worker_context (see configure_logging(get_settings())). The
    # WorkerContext deliberately doesn't expose the full Settings object
    # so workers can't grow a habit of reaching into config beyond what
    # they declare at the entrypoint. The fields read here are the only
    # IndexingService inputs that aren't already on WorkerContext.
    settings = get_settings()
    indexing_service = IndexingService(
        engine=ctx.engine,
        db_session_factory=ctx.db_session_factory,
        queue_client=ctx.queue_client,
        repo_root=str(settings.indexing.repo_root),
        extractor_kinds=tuple(settings.indexing.extractor_kinds),
        # Slice+1 TODO: rename `api.lock_acquisition_timeout_seconds` —
        # the field name reads "the API's timeout" but post-2026-05-26
        # amendment the LOCK lives in this worker, not the API. Keep the
        # field for slice (would force a Plan 3 sub-amendment + env-var
        # rename that's out of scope for the Plan 11 P0 fix). The
        # nested-config shape (`settings.api.lock_acquisition_timeout_seconds`)
        # is locked by the 2026-05-25 Plan 3 amendment (ApiConfig).
        advisory_lock_timeout_seconds=settings.api.lock_acquisition_timeout_seconds,
    )
    consumer = ReindexOrchestratorConsumer(
        queue_client=ctx.queue_client,
        retry_policy=ctx.retry_policy,
        consumer_name=ctx.consumer_name,
        stop_event=ctx.stop_event,
        indexing_service=indexing_service,
    )
    try:
        await consumer.run()
    finally:
        await teardown_worker_context(ctx)


if __name__ == "__main__":
    asyncio.run(amain())
