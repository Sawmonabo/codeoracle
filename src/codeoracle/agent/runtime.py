"""Shared runtime helpers for the explain-symbol agent graph."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol

from codeoracle.agent.state import AgentState
from codeoracle.core.telemetry import get_tracer
from codeoracle.models import DraftCitation, EvidenceHandle

type NodeUpdate = dict[str, Any]
type NodeFn = Callable[[AgentState, Any], Awaitable[NodeUpdate]]


class HandleResolverProtocol(Protocol):
    async def resolve(self, handle: EvidenceHandle) -> str: ...


SPAN_INTENT = "intent.parse"
SPAN_GRAPH_QUERY = "graph.query"
SPAN_EMBED_QUERY = "embed.query"
SPAN_COMPOSE = "llm.compose"
SPAN_VERIFY = "verifier.verify"

_TRACER = get_tracer(__name__)


def agent_node(span_name: str | None) -> Callable[[NodeFn], NodeFn]:
    """Wrap an agent node with terminal short-circuit, span, and failure
    capture."""

    def decorator(fn: NodeFn) -> NodeFn:
        @wraps(fn)
        async def wrapped(state: AgentState, deps: Any) -> NodeUpdate:
            if (
                state.get("error") is not None
                or state.get("verdict") is not None
            ):
                return {}
            try:
                if span_name is None:
                    return await fn(state, deps)
                node_name = getattr(fn, "__name__", "unknown")
                with _TRACER.start_as_current_span(span_name) as span:
                    span.set_attribute(
                        "codeoracle.agent.trace_id", state["trace_id"]
                    )
                    span.set_attribute("codeoracle.agent.node", node_name)
                    return await fn(state, deps)
            except Exception as exc:
                return {"error": str(exc)}

        return wrapped

    return decorator


async def resolve_to_citation(
    handle: EvidenceHandle, resolver: HandleResolverProtocol
) -> DraftCitation:
    """Resolve a handle through the platform resolver and wrap the excerpt."""
    with _TRACER.start_as_current_span(f"resolve.{handle.type.value}") as span:
        span.set_attribute("codeoracle.handle.type", handle.type.value)
        span.set_attribute(
            "codeoracle.handle.snapshot_sha", handle.snapshot_sha
        )
        excerpt = await resolver.resolve(handle)
    return DraftCitation(handle=handle, excerpt=excerpt)
