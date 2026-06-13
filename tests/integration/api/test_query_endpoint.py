"""§8.5 acceptance gate — /v1/query (agent delegation).

Spec §8.5 row: BL-QY01 (D4-audit-assigned) — "POST /v1/query returns
QueryResult by delegating to the agent". Plus invariant-5 503/502
mapping tests.
"""

import httpx
import pytest

from codeoracle.adapters.agent.client import AgentClient


@pytest.mark.integration
async def test_query_round_trip_to_stub_agent(api_app):
    """The API forwards the request to the stub agent and returns the
    stub's canned answer raw (no envelope).
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.post(
            "/v1/query",
            json={
                "symbol": "codeoracle.core.security.hash_password",
                "snapshot_sha": None,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETE"
    assert (
        "stub answer for: codeoracle.core.security.hash_password"
        in body["claim"]["statement"]
    )
    assert (
        body["claim"]["citations"][0]["handle"]["snapshot_sha"] == "stub_sha"
    )
    # No envelope keys (invariant 3)
    assert "data" not in body


@pytest.mark.integration
async def test_query_503_when_agent_port_changes(api_app, monkeypatch):
    """Point the AgentClient at a port nothing listens on → 503.
    Validates invariant 5 (transport-layer failure → 503).
    """
    # Mutate app.state.resources.agent_client to point at a closed port. This
    # bypasses the lifespan and tests the route's error mapping
    # directly against the real httpx transport.
    old_client = api_app.state.resources.agent_client
    api_app.state.resources.agent_client = AgentClient(
        base_url="http://127.0.0.1:1",  # port 1 — almost always closed
        request_timeout_seconds=1.0,
        shared_token=None,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app),
            base_url="http://test",
        ) as c:
            r = await c.post(
                "/v1/query",
                json={
                    "symbol": "codeoracle.core.security.hash_password",
                    "snapshot_sha": None,
                },
            )
        assert r.status_code == 503
        assert r.json() == {
            "error": {
                "code": "AGENT_UNREACHABLE",
                "message": "Agent is unreachable.",
            }
        }
    finally:
        await api_app.state.resources.agent_client.aclose()
        api_app.state.resources.agent_client = old_client
