"""API exception-to-response mapping."""

from http import HTTPStatus
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from codeoracle.adapters.agent.errors import (
    AgentResponseError,
    AgentUnreachableError,
)
from codeoracle.api.v1.schemas.errors import ErrorDetail, ErrorResponse
from codeoracle.core.exceptions import CodeOracleError


class SnapshotNotFoundError(CodeOracleError):
    """Raised when a snapshot polling request names an unknown snapshot."""

    def __init__(self, snapshot_sha: str) -> None:
        self.snapshot_sha = snapshot_sha
        super().__init__(f"no snapshot with sha={snapshot_sha!r}")


def _error_response(
    status_code: HTTPStatus,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details)
    )
    return JSONResponse(
        status_code=status_code.value,
        content=body.model_dump(exclude_none=True),
    )


async def agent_unreachable_handler(
    _request: Any,
    _exc: AgentUnreachableError,
) -> JSONResponse:
    """Map agent transport failures to a structured 503."""

    return _error_response(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "AGENT_UNREACHABLE",
        "Agent is unreachable.",
    )


async def agent_response_error_handler(
    _request: Any,
    exc: AgentResponseError,
) -> JSONResponse:
    """Map non-2xx agent responses to a structured 502."""

    return _error_response(
        HTTPStatus.BAD_GATEWAY,
        "AGENT_RESPONSE_ERROR",
        "Agent returned an error response.",
        details={"agent_status_code": exc.status_code},
    )


async def snapshot_not_found_handler(
    _request: Any,
    exc: SnapshotNotFoundError,
) -> JSONResponse:
    """Map unknown snapshot lookups to a structured 404."""

    return _error_response(
        HTTPStatus.NOT_FOUND,
        "SNAPSHOT_NOT_FOUND",
        "Snapshot was not found.",
        details={"snapshot_sha": exc.snapshot_sha},
    )


async def request_validation_error_handler(
    _request: Any,
    exc: RequestValidationError,
) -> JSONResponse:
    """Map request validation failures to a structured 422."""

    details = {"errors": jsonable_encoder(exc.errors())}
    return _error_response(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "VALIDATION_ERROR",
        "Request validation failed.",
        details=details,
    )


async def codeoracle_error_handler(
    _request: Any,
    exc: CodeOracleError,
) -> JSONResponse:
    """Map uncategorized platform errors to a structured 500."""

    return _error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "CODEORACLE_ERROR",
        str(exc) or "CodeOracle failed to process the request.",
    )


def get_exception_handlers() -> dict[type[Exception], Any]:
    """Return FastAPI exception handlers for API initialization."""

    return {
        AgentUnreachableError: agent_unreachable_handler,
        AgentResponseError: agent_response_error_handler,
        SnapshotNotFoundError: snapshot_not_found_handler,
        RequestValidationError: request_validation_error_handler,
        CodeOracleError: codeoracle_error_handler,
    }
