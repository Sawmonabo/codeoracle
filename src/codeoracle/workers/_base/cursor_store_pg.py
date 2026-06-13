"""Postgres-backed cursor store for continuous consumers."""

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.workers._base.continuous_consumer import CursorStore


class PGCursorStore[TCursor: BaseModel](CursorStore[TCursor]):
    """Stores one Pydantic cursor per continuous-ingest source."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cursor_type: type[TCursor],
    ) -> None:
        self._session_factory = session_factory
        self._cursor_type = cursor_type

    async def get_cursor(self, source: str) -> TCursor | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT cursor_data
                    FROM traces.ingest_cursor
                    WHERE source = :source
                    """
                ),
                {"source": source},
            )
            row = result.first()
        if row is None:
            return None
        try:
            return self._cursor_type.model_validate(row.cursor_data)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"invalid cursor data for source={source!r}"
            ) from exc

    async def update_cursor(self, source: str, cursor: TCursor) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO traces.ingest_cursor
                        (source, cursor_data, updated_at)
                    VALUES (:source, :data::jsonb, now())
                    ON CONFLICT (source) DO UPDATE
                    SET cursor_data = EXCLUDED.cursor_data,
                        updated_at = now()
                    """
                ),
                {"source": source, "data": cursor.model_dump_json()},
            )
