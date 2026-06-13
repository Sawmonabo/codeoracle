"""Database dependencies for API routes."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from codeoracle.api.dependencies._state import get_api_resources


def get_engine(request: Request) -> AsyncEngine:
    """Return the API process SQLAlchemy async engine."""

    return get_api_resources(request).engine


def get_session_factory(
    request: Request,
) -> async_sessionmaker[AsyncSession]:
    """Return the async session factory bound to the API engine."""

    return get_api_resources(request).session_factory


async def get_db_session(
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """Yield one database session scoped to the current HTTP request."""

    async with get_session_factory(request)() as session:
        yield session


EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
SessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession],
    Depends(get_session_factory),
]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
