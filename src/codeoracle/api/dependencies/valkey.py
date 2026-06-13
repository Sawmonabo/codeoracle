"""Valkey dependencies for API routes."""

from typing import Annotated

from fastapi import Depends, Request

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.api.dependencies._state import get_api_resources


def get_valkey_client(request: Request) -> ValkeyQueueClient:
    """Return the long-lived Valkey queue client."""

    return get_api_resources(request).valkey_client


ValkeyClientDep = Annotated[ValkeyQueueClient, Depends(get_valkey_client)]
