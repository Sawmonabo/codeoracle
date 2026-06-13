"""Query routes for API v1."""

from fastapi import APIRouter, status

from codeoracle.api.dependencies import AgentClientDep
from codeoracle.api.v1.schemas.errors import ErrorResponse
from codeoracle.api.v1.schemas.query import QueryRequest, QueryResponse

router = APIRouter(tags=["Query"])


@router.post(
    "/query",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a code intelligence query",
    responses={
        502: {
            "model": ErrorResponse,
            "description": "Agent returned an error response.",
        },
        503: {
            "model": ErrorResponse,
            "description": "Agent is unreachable.",
        },
    },
)
async def query_endpoint(
    body: QueryRequest,
    agent_client: AgentClientDep,
) -> QueryResponse:
    """Delegate one query to the agent process."""

    return await agent_client.run(body)
