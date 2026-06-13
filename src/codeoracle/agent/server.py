"""FastAPI route handlers for the agent process."""

from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from codeoracle.agent.dependencies import build_agent_dependencies
from codeoracle.agent.graph import build_agent_graph, run_agent_query
from codeoracle.models.health import HealthResponse
from codeoracle.models.query import Query, QueryResult


def register_routes(app: FastAPI) -> None:
    """Register the agent HTTP contract on ``app``."""

    @app.post("/v1/query", response_model=QueryResult)
    async def query(query: Query, request: Request) -> QueryResult:
        async with request.app.state.session_factory() as session:
            deps = build_agent_dependencies(
                session=session,
                sourcebot=request.app.state.sourcebot,
                llm=request.app.state.llm,
                embeddings=request.app.state.embeddings,
            )
            graph = build_agent_graph(deps)
            return await run_agent_query(graph, query)

    @app.get("/v1/health", response_model=HealthResponse)
    async def health(request: Request, response: Response) -> HealthResponse:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return HealthResponse(status="ok", db="ok")
        except Exception:
            response.status_code = 503
            return HealthResponse(status="degraded", db="fail")
