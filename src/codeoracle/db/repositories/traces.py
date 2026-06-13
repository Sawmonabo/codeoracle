"""Trace span repository."""

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.models.traces import Span, SpanFilter, TimestampCursor
from codeoracle.workers._base.continuous_consumer import CursorStore

TRACES_INGEST_SOURCE = "tempo"


class TracesRepository:
    """Persistence boundary for trace spans and the Tempo cursor."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cursor_store: CursorStore[TimestampCursor],
    ) -> None:
        self._session_factory = session_factory
        self._cursor_store = cursor_store

    async def upsert_spans(self, spans: Sequence[Span]) -> int:
        """Persist spans idempotently and return inserted row count."""
        if not spans:
            return 0

        rows = [
            {
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "service_name": span.service_name,
                "operation_name": span.operation_name,
                "kind": span.kind,
                "start_unix_nano": span.start_unix_nano,
                "end_unix_nano": span.end_unix_nano,
                "status_code": span.status_code,
                "status_message": span.status_message,
                "attributes": json.dumps(span.attributes),
                "scope_name": span.scope_name,
                "scope_version": span.scope_version,
            }
            for span in spans
        ]
        stmt = text(
            """
            INSERT INTO traces.spans (
                trace_id,
                span_id,
                parent_span_id,
                service_name,
                operation_name,
                kind,
                start_unix_nano,
                end_unix_nano,
                status_code,
                status_message,
                attributes,
                scope_name,
                scope_version
            )
            VALUES (
                :trace_id,
                :span_id,
                :parent_span_id,
                :service_name,
                :operation_name,
                :kind,
                :start_unix_nano,
                :end_unix_nano,
                :status_code,
                :status_message,
                CAST(:attributes AS jsonb),
                :scope_name,
                :scope_version
            )
            ON CONFLICT (trace_id, span_id) DO NOTHING
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.execute(stmt, rows)
        return max(getattr(result, "rowcount", 0) or 0, 0)

    async def find_spans(self, span_filter: SpanFilter) -> list[Span]:
        if span_filter.is_trace_lookup():
            assert span_filter.trace_id is not None
            return await self._find_by_trace_id(
                trace_id=span_filter.trace_id,
                limit=span_filter.limit,
            )
        return await self._find_by_window(span_filter)

    async def get_cursor(self) -> TimestampCursor | None:
        return await self._cursor_store.get_cursor(TRACES_INGEST_SOURCE)

    async def update_cursor(self, cursor: TimestampCursor) -> None:
        await self._cursor_store.update_cursor(TRACES_INGEST_SOURCE, cursor)

    async def _find_by_trace_id(
        self, *, trace_id: bytes, limit: int
    ) -> list[Span]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT *
                    FROM traces.spans
                    WHERE trace_id = :trace_id
                    ORDER BY start_unix_nano ASC
                    LIMIT :limit
                    """
                ),
                {"trace_id": trace_id, "limit": limit},
            )
            return [_row_to_span(row._mapping) for row in result.all()]

    async def _find_by_window(self, span_filter: SpanFilter) -> list[Span]:
        assert span_filter.service_name is not None
        assert span_filter.start_unix_nano is not None
        assert span_filter.end_unix_nano is not None

        predicates = [
            "service_name = :service_name",
            "start_unix_nano BETWEEN :start_unix_nano AND :end_unix_nano",
        ]
        params: dict[str, Any] = {
            "service_name": span_filter.service_name,
            "start_unix_nano": span_filter.start_unix_nano,
            "end_unix_nano": span_filter.end_unix_nano,
            "limit": span_filter.limit,
        }
        if span_filter.route is not None:
            predicates.append("attributes @> CAST(:route_attrs AS jsonb)")
            params["route_attrs"] = json.dumps(
                {"http.route": span_filter.route}
            )

        sql = f"""
            SELECT *
            FROM traces.spans
            WHERE {" AND ".join(predicates)}
            ORDER BY start_unix_nano ASC
            LIMIT :limit
        """
        async with self._session_factory() as session:
            result = await session.execute(text(sql), params)
            return [_row_to_span(row._mapping) for row in result.all()]


def _row_to_span(row: Any) -> Span:
    return Span(
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        parent_span_id=row["parent_span_id"],
        service_name=row["service_name"],
        operation_name=row["operation_name"],
        kind=row["kind"],
        start_unix_nano=row["start_unix_nano"],
        end_unix_nano=row["end_unix_nano"],
        status_code=row["status_code"],
        status_message=row["status_message"],
        attributes=row["attributes"] or {},
        scope_name=row["scope_name"],
        scope_version=row["scope_version"],
    )
