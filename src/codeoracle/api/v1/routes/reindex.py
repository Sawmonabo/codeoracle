"""Reindex routes for API v1."""

import logging
from http import HTTPStatus

from fastapi import APIRouter, status

from codeoracle.api.dependencies import IndexingServiceDep
from codeoracle.api.errors import SnapshotNotFoundError
from codeoracle.api.v1.schemas.errors import ErrorResponse
from codeoracle.api.v1.schemas.reindex import ReindexRequest
from codeoracle.api.v1.schemas.snapshot import SnapshotResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reindex", tags=["Reindex"])


@router.post(
    "",
    response_model=SnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a reindex",
    responses={
        500: {
            "model": ErrorResponse,
            "description": "Reindex request could not be enqueued.",
        },
    },
)
async def reindex_endpoint(
    body: ReindexRequest,
    indexing_service: IndexingServiceDep,
) -> SnapshotResponse:
    """Enqueue an async reindex and return the pending snapshot."""

    snap = await indexing_service.enqueue_reindex(base_sha=body.base_sha)
    logger.info(
        "reindex_enqueued",
        extra={"snapshot_sha": snap.sha, "status": snap.status.value},
    )
    return SnapshotResponse.model_validate(snap)


@router.get(
    "/{snapshot_sha}",
    response_model=SnapshotResponse,
    summary="Get reindex status",
    responses={
        HTTPStatus.NOT_FOUND.value: {
            "model": ErrorResponse,
            "description": "Snapshot was not found.",
        },
    },
)
async def reindex_status_endpoint(
    snapshot_sha: str,
    indexing_service: IndexingServiceDep,
) -> SnapshotResponse:
    """Return the snapshot lifecycle row for a reindex request."""

    snap = await indexing_service.get_snapshot(snapshot_sha)
    if snap is None:
        raise SnapshotNotFoundError(snapshot_sha)
    return SnapshotResponse.model_validate(snap)
