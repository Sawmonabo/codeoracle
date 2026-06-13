"""Health-check routes for API v1."""

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from codeoracle.api.dependencies import DbSessionDep, ValkeyClientDep
from codeoracle.api.v1.schemas.health import (
    HealthLiveResponse,
    HealthReadyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=HealthLiveResponse,
    summary="Liveness probe",
    description="Check that the API process can answer HTTP requests.",
)
async def liveness() -> HealthLiveResponse:
    """Return process liveness without checking external dependencies."""

    return HealthLiveResponse()


async def _readiness_status(
    db: DbSessionDep,
    valkey_client: ValkeyClientDep,
) -> HealthReadyResponse:
    db_status = "ok"
    valkey_status = "ok"

    try:
        result = await db.execute(text("SELECT 1"))
        value = result.scalar_one()
        if value != 1:
            logger.warning("health_db_ping_unexpected", extra={"value": value})
            db_status = "fail"
    except Exception as exc:
        logger.warning("health_db_ping_failed", extra={"error": str(exc)})
        db_status = "fail"

    try:
        await valkey_client.ping()
    except Exception as exc:
        logger.warning("health_valkey_ping_failed", extra={"error": str(exc)})
        valkey_status = "fail"

    overall = (
        "ok" if db_status == "ok" and valkey_status == "ok" else "degraded"
    )
    return HealthReadyResponse(
        status=overall,
        db=db_status,
        valkey=valkey_status,
    )


def _set_readiness_status_code(
    response: Response,
    body: HealthReadyResponse,
) -> None:
    if body.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE


@router.get(
    "/ready",
    response_model=HealthReadyResponse,
    summary="Readiness probe",
    description="Check API-owned DB and Valkey dependencies.",
)
async def readiness(
    response: Response,
    db: DbSessionDep,
    valkey_client: ValkeyClientDep,
) -> HealthReadyResponse:
    """Return readiness based on DB and Valkey availability."""

    body = await _readiness_status(db, valkey_client)
    _set_readiness_status_code(response, body)
    return body


@router.get(
    "",
    response_model=HealthReadyResponse,
    summary="Compatibility readiness probe",
    description="Compatibility alias for /v1/health/ready.",
)
async def health(
    response: Response,
    db: DbSessionDep,
    valkey_client: ValkeyClientDep,
) -> HealthReadyResponse:
    """Compatibility alias for the readiness probe."""

    body = await _readiness_status(db, valkey_client)
    _set_readiness_status_code(response, body)
    return body
