"""Plan 10 Task 2 — AgentState declares exactly the eleven pipeline keys."""

from codeoracle.agent.state import AgentState


def test_agent_state_declares_all_pipeline_keys() -> None:
    expected = {
        "query",
        "trace_id",
        "snapshot_sha",
        "plan_handle_types",
        "symbol_found",
        "graph_citations",
        "embed_citations",
        "draft_claim",
        "claim",
        "verdict",
        "error",
    }
    assert set(AgentState.__annotations__) == expected
