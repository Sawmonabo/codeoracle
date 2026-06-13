"""
Plan 10 Task 4 — the intent node: deterministic validation + snapshot
resolution.
"""

from codeoracle.agent.nodes.intent import (
    EXPLAIN_SYMBOL_HANDLE_TYPES,
    intent_node,
)
from codeoracle.models import HandleType, Query, SnapshotStatus


async def test_intent_resolves_latest_snapshot_when_query_unpinned(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(latest_sha="deadbeef")
    state = make_agent_state(
        query=Query(symbol="codeoracle.agent.graph.run_agent_query")
    )
    update = await intent_node(state, deps)
    assert update["snapshot_sha"] == "deadbeef"
    assert update["plan_handle_types"] == [
        HandleType.CODE_SPAN,
        HandleType.DOC_PARAGRAPH,
        HandleType.GRAPH_QUERY,
    ]


async def test_intent_honors_explicit_snapshot_pin(
    make_fake_deps, make_agent_state
) -> None:
    # The pinned SHA is resolved via get_by_sha and must be APPLIED (default
    # fake).
    deps = make_fake_deps(latest_sha="deadbeef")
    state = make_agent_state(
        query=Query(symbol="codeoracle.x.y", snapshot_sha="pinned99")
    )
    update = await intent_node(state, deps)
    assert update["snapshot_sha"] == "pinned99"  # the pin wins over the latest


async def test_intent_rejects_pinned_snapshot_that_is_not_applied(
    make_fake_deps, make_agent_state
) -> None:
    # A pin to a non-APPLIED SHA must NOT silently query a half-applied corpus.
    deps = make_fake_deps(pinned_status=SnapshotStatus.PENDING)
    state = make_agent_state(
        query=Query(symbol="codeoracle.x.y", snapshot_sha="pending99")
    )
    update = await intent_node(state, deps)
    assert "is not APPLIED" in str(update["error"])
    assert "snapshot_sha" not in update


async def test_intent_rejects_pinned_snapshot_that_is_missing(
    make_fake_deps, make_agent_state
) -> None:
    # A pin to a SHA with no snapshot row is rejected, not passed through.
    deps = make_fake_deps(pinned_status=None)
    state = make_agent_state(
        query=Query(symbol="codeoracle.x.y", snapshot_sha="ghost99")
    )
    update = await intent_node(state, deps)
    assert "is not APPLIED" in str(update["error"])
    assert "snapshot_sha" not in update


async def test_intent_errors_when_no_snapshot_exists(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(has_snapshot=False)
    state = make_agent_state(query=Query(symbol="codeoracle.x.y"))
    update = await intent_node(state, deps)
    assert "no APPLIED snapshot" in str(update["error"])


async def test_intent_rejects_injection_unsafe_symbol(
    make_fake_deps, make_agent_state
) -> None:
    # Query accepts this (it is only length-bounded); the agent's FQN guard
    # must not.
    deps = make_fake_deps()
    state = make_agent_state(query=Query(symbol="x'}) DETACH DELETE (n) //"))
    update = await intent_node(state, deps)
    assert "injection-safe" in str(update["error"])
    # the unsafe symbol never reaches snapshot resolution
    assert "snapshot_sha" not in update


async def test_intent_never_plans_stub_handle_types(
    make_fake_deps, make_agent_state
) -> None:
    """§8.5 — test_intent_classifier_never_produces_unsupported_handle_types.

    For every symbol, the planned handle types exclude TRACE_SPAN / DB_RECORD.
    """
    deps = make_fake_deps()
    for symbol in (
        "codeoracle.a.b",
        "codeoracle.c.d.e",
        "module.func()",
        "pkg.Class#",
        "single",
    ):
        update = await intent_node(
            make_agent_state(query=Query(symbol=symbol)), deps
        )
        planned = update["plan_handle_types"]
        assert isinstance(planned, list)
        assert HandleType.TRACE_SPAN not in planned
        assert HandleType.DB_RECORD not in planned


def test_explain_symbol_handle_types_constant() -> None:
    assert EXPLAIN_SYMBOL_HANDLE_TYPES == [
        HandleType.CODE_SPAN,
        HandleType.DOC_PARAGRAPH,
        HandleType.GRAPH_QUERY,
    ]
