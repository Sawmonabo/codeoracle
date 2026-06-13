"""API v1 router aggregation."""

from fastapi import APIRouter

from codeoracle.api.v1.routes import health, query, reindex

router = APIRouter(prefix="/v1")

router.include_router(health.router)
router.include_router(query.router)
router.include_router(reindex.router)
