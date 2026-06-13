"""CodeOracle agent process.

A LangGraph state machine that turns an ``explain_symbol`` query into a
verified
``QueryResult``. ``run_agent_query`` is the entry point; ``build_agent_graph``
is the per-query factory the agent process's FastAPI route handler (Task 11)
calls per request via ``build_agent_dependencies``.
"""

from codeoracle.agent.dependencies import (
    AgentDependencies,
    build_agent_dependencies,
)
from codeoracle.agent.graph import build_agent_graph, run_agent_query
from codeoracle.agent.state import AgentState

__all__ = [
    "AgentDependencies",
    "AgentState",
    "build_agent_dependencies",
    "build_agent_graph",
    "run_agent_query",
]
