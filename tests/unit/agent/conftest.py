"""Shared fakes + factories for agent unit tests.

The fakes implement exactly the methods the agent calls, with the signatures
verified against Plans 4-9. Tests configure them through ``make_fake_deps`` /
``make_agent_state`` keyword arguments; the fake classes are private.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

import pytest

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.state import AgentState
from codeoracle.models import (
    Citation,
    Claim,
    DraftClaim,
    EmbeddingHit,
    EvidenceHandle,
    HandleType,
    Query,
    Snapshot,
    SnapshotStatus,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)

_DEFAULT_SHA = "abc1234"


# --- fake collaborators -----------------------------------------------------


class _FakeGraphRepo:
    """Fakes GraphRepository.execute_cypher. The agent calls execute_cypher
    exactly once per query — the symbol-existence check — so the fake returns
    the configured existence rows for any call and records what it was asked.
    (The callers query reaches the DB through the resolver, never this repo.)
    """

    def __init__(self, *, exists_rows: list[dict[str, object]]) -> None:
        self._exists_rows = exists_rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_cypher(
        self,
        cypher_body: str,
        params: dict[str, Any] | None = None,
        *,
        column_spec: str = "result agtype",
    ) -> list[dict[str, object]]:
        _ = column_spec
        self.calls.append((cypher_body, params or {}))
        return self._exists_rows


class _FakeEmbeddingsRepo:
    def __init__(self, *, hits: Sequence[EmbeddingHit]) -> None:
        self._hits = list(hits)

    async def knn(
        self,
        *,
        query_embedding: list[float],
        snapshot_sha: str,
        k: int,
    ) -> list[EmbeddingHit]:
        # Keyword-only signature mirrors Plan 6's EmbeddingsRepository.knn
        # contract (P0-17). A positional call from the agent would TypeError
        # at unit-test time, failing the suite green before signature drift
        # could ship.
        return self._hits[:k]


class _FakeSnapshotRepo:
    def __init__(
        self, *, latest: Snapshot | None, pinned_status: SnapshotStatus | None
    ) -> None:
        self._latest = latest
        self._pinned_status = pinned_status

    async def get_latest_applied(self) -> Snapshot | None:
        return self._latest

    async def get_by_sha(self, sha: str) -> Snapshot | None:
        # Mirrors the real repo's WHERE sha == :sha: the returned snapshot
        # carries
        # the queried sha. ``pinned_status`` None models "no such row" so tests
        # exercise the intent node's APPLIED gate (missing + non-APPLIED) on
        # the
        # pinned path.
        if self._pinned_status is None:
            return None
        return Snapshot(sha=sha, status=self._pinned_status)


class _FakeResolver:
    """Fakes HandleResolverService.resolve. Returns ``<content>::<locator>`` so
    every citation gets a distinct, deterministic excerpt; or raises."""

    def __init__(
        self, *, content: str, raise_exc: Exception | None = None
    ) -> None:
        self._content = content
        self._raise = raise_exc
        self.resolved: list[EvidenceHandle] = []

    async def resolve(self, handle: EvidenceHandle) -> str:
        if self._raise is not None:
            raise self._raise
        self.resolved.append(handle)
        return f"{self._content}::{handle.locator}"


class _FakeVerifier:
    def __init__(self, *, verdict: Verdict) -> None:
        self._verdict = verdict
        self.verified: list[Claim] = []

    async def verify(self, claim: Claim) -> Verdict:
        self.verified.append(claim)
        return self._verdict


class _FakeTrustMatrix:
    """
    Mirrors the real synchronous ``score_each(draft, *, source_age_days=0)``;
    assigns a constant trust of 7 to every citation."""

    def score_each(
        self, draft: DraftClaim, *, source_age_days: int = 0
    ) -> Claim:
        return Claim(
            statement=draft.statement,
            citations=[
                Citation(handle=c.handle, excerpt=c.excerpt, source_trust=7)
                for c in draft.citations
            ],
            confidence=draft.confidence,
            contradictions=[
                Citation(handle=c.handle, excerpt=c.excerpt, source_trust=7)
                for c in draft.contradictions
            ],
        )


class _FakeLLM:
    def __init__(self, *, response: ChatResponse) -> None:
        self._response = response
        self.messages_seen: list[Any] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        self.messages_seen = list(messages)
        return self._response

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class _FakeEmbeddings:
    def __init__(self, *, vector: Sequence[float]) -> None:
        self._vector = list(vector)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


# --- builders ---------------------------------------------------------------


def default_tool_args(
    citation_indices: Sequence[int] = (0, 1, 2),
    contradiction_indices: Sequence[int] = (),
    *,
    statement: str = "The symbol resolves and checks each evidence citation.",
    confidence: float = 0.9,
) -> str:
    """A well-formed ``submit_explanation`` arguments JSON string."""
    return json.dumps(
        {
            "statement": statement,
            "confidence": confidence,
            "citation_indices": list(citation_indices),
            "contradiction_indices": list(contradiction_indices),
        }
    )


def chat_response(
    arguments_json: str, *, tool_name: str = "submit_explanation"
) -> ChatResponse:
    """A ChatResponse whose single tool call carries ``arguments_json``."""
    return ChatResponse(
        content="",
        usage=TokenUsage(input_tokens=20, output_tokens=20),
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(
                id="call_1", name=tool_name, arguments_json=arguments_json
            )
        ],
    )


def chat_response_no_tool_call() -> ChatResponse:
    """
    A ChatResponse with prose only — no tool call (a compose failure case).
    """
    return ChatResponse(
        content="I cannot determine this.",
        usage=TokenUsage(input_tokens=20, output_tokens=5),
        finish_reason="stop",
        tool_calls=[],
    )


def _default_hits() -> list[EmbeddingHit]:
    return [
        EmbeddingHit(
            snapshot_sha=_DEFAULT_SHA,
            source_path="src/codeoracle/services/verifier/service.py",
            chunk_start_line=10 + 20 * i,
            chunk_end_line=25 + 20 * i,
            content=f"chunk {i} body",
            distance=0.1 * (i + 1),
        )
        for i in range(3)
    ]


@dataclass(frozen=True, kw_only=True)
class _FakeDepsOptions:
    symbol_exists: bool = True
    knn_hits: list[EmbeddingHit] | None = None
    resolve_content: str = "<resolved>"
    resolve_raises: Exception | None = None
    has_snapshot: bool = True
    latest_sha: str = _DEFAULT_SHA
    pinned_status: SnapshotStatus | None = SnapshotStatus.APPLIED
    llm_response: ChatResponse | None = None
    llm_tool_args: str | None = None
    verdict: Verdict | None = None
    embed_vector: Sequence[float] = (0.1, 0.2, 0.3)


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def make_fake_deps() -> Callable[..., AgentDependencies]:
    """Return a factory producing an AgentDependencies of configurable fakes.

    Keyword arguments (all optional):
      symbol_exists      — graph existence check returns a row (default True)
      knn_hits           — explicit list[EmbeddingHit] (default 3 hits)
      resolve_content    — resolver excerpt prefix; resolve_raises — make it
      raise
      has_snapshot       — get_latest_applied returns a Snapshot (default True)
      latest_sha         — that snapshot's sha
      pinned_status      — status of the snapshot get_by_sha returns for a
      pinned SHA (default APPLIED); None → get_by_sha returns None
      llm_response       — explicit ChatResponse; llm_tool_args — explicit args
      JSON verdict            — the verdict FakeVerifier.verify returns
    """

    def _make(**overrides: Any) -> AgentDependencies:
        options = _FakeDepsOptions(**overrides)
        llm_response = options.llm_response
        if llm_response is None:
            args = (
                options.llm_tool_args
                if options.llm_tool_args is not None
                else default_tool_args()
            )
            llm_response = chat_response(args)
        verdict = options.verdict
        if verdict is None:
            verdict = Verdict(
                status=VerdictStatus.PROVEN,
                reason="every citation resolved and matched",
                resolved_citations=[],
            )
        snapshot = (
            Snapshot(sha=options.latest_sha) if options.has_snapshot else None
        )
        return AgentDependencies(
            graph_repo=_FakeGraphRepo(
                exists_rows=[{"fqn": "found"}]
                if options.symbol_exists
                else [],
            ),
            embeddings_repo=_FakeEmbeddingsRepo(
                hits=(
                    options.knn_hits
                    if options.knn_hits is not None
                    else _default_hits()
                )
            ),
            snapshot_repo=_FakeSnapshotRepo(
                latest=snapshot, pinned_status=options.pinned_status
            ),
            resolver=_FakeResolver(
                content=options.resolve_content,
                raise_exc=options.resolve_raises,
            ),
            verifier=_FakeVerifier(verdict=verdict),
            trust_matrix=_FakeTrustMatrix(),
            llm=_FakeLLM(response=llm_response),
            embeddings=_FakeEmbeddings(vector=options.embed_vector),
        )

    return _make


@pytest.fixture
def make_agent_state() -> Callable[..., AgentState]:
    """Return a factory producing an AgentState seeded like ``run_agent_query``
    does — every key present, overridable by keyword."""

    def _make(**overrides: Any) -> AgentState:
        state: dict[str, Any] = {
            "query": Query(
                symbol="codeoracle.services.verifier.service.VerifierService.verify"
            ),
            "trace_id": "trace-test",
            "snapshot_sha": _DEFAULT_SHA,
            "plan_handle_types": [
                HandleType.CODE_SPAN,
                HandleType.DOC_PARAGRAPH,
                HandleType.GRAPH_QUERY,
            ],
            "symbol_found": False,
            "graph_citations": [],
            "embed_citations": [],
            "draft_claim": None,
            "claim": None,
            "verdict": None,
            "error": None,
        }
        state.update(overrides)
        return cast(AgentState, state)

    return _make
