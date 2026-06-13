"""Indexing-service dependencies for API routes."""

from typing import Annotated

from fastapi import Depends, Request

from codeoracle.api.dependencies._state import get_api_resources
from codeoracle.services.indexing.service import IndexingService


def get_indexing_service(request: Request) -> IndexingService:
    """Return the API process indexing service."""

    return get_api_resources(request).indexing_service


IndexingServiceDep = Annotated[
    IndexingService,
    Depends(get_indexing_service),
]
