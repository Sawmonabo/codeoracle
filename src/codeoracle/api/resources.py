"""Typed resources owned by the API lifespan."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import Settings
from codeoracle.services.indexing.service import IndexingService


@dataclass(slots=True)
class ApiResources:
    """Long-lived resources initialized once per API process."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    valkey_client: ValkeyQueueClient
    indexing_service: IndexingService
    agent_client: AgentClient
