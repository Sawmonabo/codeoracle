"""Query request and response schemas for API v1."""

from codeoracle.models.query import Query, QueryResult

QueryRequest = Query
QueryResponse = QueryResult

__all__ = ["QueryRequest", "QueryResponse"]
