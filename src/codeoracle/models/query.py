"""The platform's public query types.

Exposed by the API (`POST /v1/query`, Plan 11) and the MCP server's
`explain_symbol` tool (Plan 12). Plan 3 ships the types so the agent (Plan 10)
and the consumer services (Plans 8-9) can build against them.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from codeoracle.models.evidence import Claim, Verdict


class QueryStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class Query(BaseModel):
    """Input to /v1/query.

    `type` discriminates the query family. For the slice, only `explain_symbol`
    is supported. Plan 10 (LangGraph agent) will validate `type` against a
    closed enum once the intent classifier lands.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["explain_symbol"] = "explain_symbol"
    symbol: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    corpus: Literal["self"] = (
        "self"  # slice supports only the platform's own source
    )
    snapshot_sha: str | None = (
        None  # None → resolve at execution time to current HEAD
    )


class QueryResult(BaseModel):
    """Output of /v1/query."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    status: QueryStatus
    claim: Claim | None = None
    verdict: Verdict | None = None
    error: str | None = None

    @classmethod
    def proven(
        cls, trace_id: str, claim: Claim, verdict: Verdict
    ) -> QueryResult:
        return cls(
            trace_id=trace_id,
            status=QueryStatus.COMPLETE,
            claim=claim,
            verdict=verdict,
        )

    @classmethod
    def failed(cls, trace_id: str, error: str) -> QueryResult:
        return cls(trace_id=trace_id, status=QueryStatus.FAILED, error=error)
