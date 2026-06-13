"""Plan 10 Task 9 — end-to-end: build_agent_graph + run_agent_query.

Every test drives the whole six-node graph against fully-faked collaborators —
except ``test_low_confidence_surfaces_unknown_through_the_real_floor``, which
swaps in the REAL ``VerifierService`` (over the fake resolver) so the
agent-level
confidence-floor path is pinned against Plan 9's actual floor, not a fake's
copy of it (D-10.U8).
"""

import dataclasses
import json

from codeoracle.agent.graph import build_agent_graph, run_agent_query
from codeoracle.models import Query, QueryStatus, Verdict, VerdictStatus
from codeoracle.models.llm import ChatResponse, TokenUsage, ToolCall
from codeoracle.services.verifier.service import (
    PROVEN_CONFIDENCE_FLOOR,
    VerifierService,
)


async def test_proven_path_returns_complete_with_a_verified_claim(
    make_fake_deps,
) -> None:
    deps = (
        make_fake_deps()
    )  # defaults: symbol exists, 3 knn hits, PROVEN verdict
    graph = build_agent_graph(deps)
    result = await run_agent_query(
        graph, Query(symbol="codeoracle.services.x.X.method")
    )
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.PROVEN
    assert result.claim is not None
    # §8.5 — PROVEN with >= 3 citations spanning >= 2 handle types
    assert len(result.claim.citations) >= 3
    assert len({c.handle.type for c in result.claim.citations}) >= 2
    assert result.error is None
    assert result.trace_id  # a non-empty trace id


async def test_missing_symbol_returns_complete_with_unknown_verdict(
    make_fake_deps,
) -> None:
    deps = make_fake_deps(symbol_exists=False)
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.deleted.symbol")
    )
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.UNKNOWN
    # §8.7 ceremony point 4 — the exact reason string
    assert (
        result.verdict.reason
        == "symbol not found in graph at snapshot abc1234"
    )
    assert result.claim is None  # no claim on the UNKNOWN path
    assert result.error is None


async def test_refuted_verdict_passes_through_verbatim(make_fake_deps) -> None:
    refuted = Verdict(
        status=VerdictStatus.REFUTED,
        reason="excerpt tampered",
        resolved_citations=[],
    )
    deps = make_fake_deps(verdict=refuted)
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.x.y")
    )
    # invariant 7 — the agent reports REFUTED as REFUTED; the query still
    # COMPLETEd
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.REFUTED


async def test_low_confidence_surfaces_unknown_through_the_real_floor(
    make_fake_deps,
) -> None:
    """An LLM ``confidence`` below ``PROVEN_CONFIDENCE_FLOOR`` makes the agent
    surface UNKNOWN — pinned end-to-end against the REAL ``VerifierService``,
    not a floor-aware fake (D-10.U8).

    The floor lives in Plan 9 (``VerifierService.verify`` abstains below it)
    and is unit-tested there; this test pins the *agent-level* path the slice
    actually runs — compose authors a low confidence, ``score`` still builds
    the
    Claim, and ``verify`` returns UNKNOWN. Wiring the real verifier (over the
    fake resolver) and importing the real ``PROVEN_CONFIDENCE_FLOOR`` constant
    means a future retune of the floor cannot leave this test green against a
    stale literal (the D-10.U6 failure mode). The confidence is driven below
    the floor by feeding compose a ``submit_explanation`` tool call whose
    ``confidence`` is derived from the imported constant — no node is modified.

    Verdict precedence note: this is the
    ``test_refuted_verdict_passes_through_verbatim``
    shape, NOT the symbol-not-found shape. Because the full pipeline runs, the
    Claim IS present (``score`` produced it) and ``run_agent_query`` routes
    through ``QueryResult.proven`` — which forwards a non-PROVEN verdict
    verbatim. So ``claim`` is non-``None`` here; only the verdict is UNKNOWN.
    """
    # A well-formed submit_explanation tool call whose confidence is driven
    # below the real floor (no magic number — derived from the imported
    # constant, so a future retune cannot leave a stale literal green).
    low_conf_args = json.dumps(
        {
            "statement": "The symbol resolves but the evidence is weak.",
            "confidence": PROVEN_CONFIDENCE_FLOOR - 0.2,
            "citation_indices": [0, 1, 2],
            "contradiction_indices": [],
        }
    )
    low_conf_response = ChatResponse(
        content="",
        usage=TokenUsage(input_tokens=20, output_tokens=20),
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(
                id="call_1",
                name="submit_explanation",
                arguments_json=low_conf_args,
            )
        ],
    )
    base = make_fake_deps(llm_response=low_conf_response)
    # Replace ONLY the verifier with the real service (frozen+slots dataclass →
    # dataclasses.replace), keeping the fake resolver the real service resolves
    # through. Every other collaborator stays a fake.
    deps = dataclasses.replace(
        base, verifier=VerifierService(resolver=base.resolver)
    )
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.x.y")
    )
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.UNKNOWN
    # the Claim is present — the floor abstains at the VERDICT, not by dropping
    # the claim (contrast the symbol-not-found path, where claim is None)
    assert result.claim is not None


async def test_compose_failure_returns_failed(make_fake_deps) -> None:
    no_call = ChatResponse(
        content="cannot help",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        finish_reason="stop",
        tool_calls=[],
    )
    deps = make_fake_deps(llm_response=no_call)
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.x.y")
    )
    assert result.status == QueryStatus.FAILED
    assert result.claim is None
    assert "submit_explanation" in str(result.error)


async def test_run_agent_query_never_raises_on_collaborator_crash(
    make_fake_deps,
) -> None:
    # the resolver raises inside graph_query — the agent must still return a
    # QueryResult
    deps = make_fake_deps(resolve_raises=RuntimeError("sourcebot is down"))
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.x.y")
    )
    assert (
        result.status == QueryStatus.FAILED
    )  # invariant 6 — no exception escapes
    assert "sourcebot is down" in str(result.error)


async def test_injection_unsafe_symbol_returns_failed(make_fake_deps) -> None:
    deps = make_fake_deps()
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="x'}) DETACH DELETE (n) //")
    )
    assert result.status == QueryStatus.FAILED
    assert "injection-safe" in str(result.error)


async def test_missing_snapshot_returns_failed(make_fake_deps) -> None:
    deps = make_fake_deps(has_snapshot=False)
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.x.y")
    )
    assert result.status == QueryStatus.FAILED
    assert "no APPLIED snapshot" in str(result.error)


async def test_each_query_gets_a_distinct_trace_id(make_fake_deps) -> None:
    deps = make_fake_deps()
    r1 = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.a.b")
    )
    r2 = await run_agent_query(
        build_agent_graph(deps), Query(symbol="codeoracle.a.b")
    )
    assert r1.trace_id
    assert r2.trace_id
    assert r1.trace_id != r2.trace_id


async def test_build_agent_graph_returns_an_invocable_graph(
    make_fake_deps,
) -> None:
    graph = build_agent_graph(make_fake_deps())
    assert hasattr(graph, "ainvoke")
