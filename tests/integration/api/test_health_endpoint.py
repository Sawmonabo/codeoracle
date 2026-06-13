"""§8.5 acceptance gate — /v1/health readiness.

Spec §8.5 row: BL-HC01 (or whichever ID is assigned during Task #18 — D4
audit) — "API /v1/health returns ok with db=ok when DB is reachable".
"""

import httpx
import pytest


@pytest.mark.integration
async def test_health_returns_ok_with_real_db(api_app):
    """Full process: DB + Valkey readiness returns 200."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "valkey": "ok"}
