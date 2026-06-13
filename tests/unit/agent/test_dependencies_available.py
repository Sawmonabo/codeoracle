"""
Plan 10 Task 1 — the new third-party dependencies are installed and importable.
"""

from langgraph.graph import END, START, StateGraph
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


def test_langgraph_state_graph_importable() -> None:
    assert StateGraph is not None
    assert START
    assert END


def test_opentelemetry_api_importable() -> None:
    assert Status is not None
    assert StatusCode is not None
    # get_tracer must return a usable (no-op until an SDK provider is set)
    # tracer.
    tracer = trace.get_tracer("codeoracle.agent.test")
    with tracer.start_as_current_span("smoke"):
        pass
