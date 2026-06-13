"""Agent-client dependencies for API routes."""

from typing import Annotated

from fastapi import Depends, Request

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.api.dependencies._state import get_api_resources


def get_agent_client(request: Request) -> AgentClient:
    """Return the long-lived HTTP client for the agent process."""

    return get_api_resources(request).agent_client


AgentClientDep = Annotated[AgentClient, Depends(get_agent_client)]
