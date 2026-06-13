"""Route-facing API dependency exports."""

from codeoracle.api.dependencies._state import (
    get_api_resources,
    require_app_state,
)
from codeoracle.api.dependencies.agent import (
    AgentClientDep,
    get_agent_client,
)
from codeoracle.api.dependencies.database import (
    DbSessionDep,
    EngineDep,
    SessionFactoryDep,
    get_db_session,
    get_engine,
    get_session_factory,
)
from codeoracle.api.dependencies.indexing import (
    IndexingServiceDep,
    get_indexing_service,
)
from codeoracle.api.dependencies.request import (
    CorrelationIdDep,
    TraceIdDep,
    get_correlation_id,
    get_trace_id,
)
from codeoracle.api.dependencies.settings import (
    SettingsDep,
    get_settings_dep,
)
from codeoracle.api.dependencies.valkey import (
    ValkeyClientDep,
    get_valkey_client,
)

__all__ = [
    "AgentClientDep",
    "CorrelationIdDep",
    "DbSessionDep",
    "EngineDep",
    "IndexingServiceDep",
    "SessionFactoryDep",
    "SettingsDep",
    "TraceIdDep",
    "ValkeyClientDep",
    "get_agent_client",
    "get_api_resources",
    "get_correlation_id",
    "get_db_session",
    "get_engine",
    "get_indexing_service",
    "get_session_factory",
    "get_settings_dep",
    "get_trace_id",
    "get_valkey_client",
    "require_app_state",
]
