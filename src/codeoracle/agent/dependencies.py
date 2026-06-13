"""The collaborator bundle injected into the agent graph.

``AgentDependencies`` is built once per query by the agent process's FastAPI
route handler (Task 11) via ``build_agent_dependencies(session, sourcebot,
llm, embeddings)`` in ``codeoracle/agent/dependencies.py``.
Per-request
data (the ``Query``) stays in the route-handler scope and is forwarded to
``run_agent_query(graph, query)`` directly — not to the composer. Tests use
``make_fake_deps``.
``build_agent_graph`` bakes one instance into the node closures, so a compiled
graph belongs to exactly one query (invariant 9).

The repositories are bound to a single request-scoped ``AsyncSession``. Because
the graph runs strictly sequentially (invariant 4) that session is never
touched concurrently — a parallel retrieval fan-out would corrupt it.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.models import (
    Claim,
    DraftClaim,
    EmbeddingHit,
    EvidenceHandle,
    HandleType,
    Snapshot,
    Verdict,
)
from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.handle_resolution.service import (
    HandleResolverStrategyProtocol,
)
from codeoracle.services.handle_resolution.strategies import (
    CodeSpanResolver,
    DocParagraphResolver,
    GraphQueryResolver,
    StubResolver,
)
from codeoracle.services.trust_matrix import TrustMatrixService
from codeoracle.services.verifier import VerifierService


class GraphRepoProtocol(Protocol):
    async def execute_cypher(
        self,
        cypher_body: str,
        params: dict[str, Any] | None = None,
        *,
        column_spec: str = "result agtype",
    ) -> list[dict[str, object]]: ...


class EmbeddingsRepoProtocol(Protocol):
    async def knn(
        self,
        *,
        query_embedding: list[float],
        snapshot_sha: str,
        k: int,
    ) -> list[EmbeddingHit]: ...


class SnapshotRepoProtocol(Protocol):
    async def get_latest_applied(self) -> Snapshot | None: ...

    async def get_by_sha(self, sha: str) -> Snapshot | None: ...


class HandleResolverProtocol(Protocol):
    async def resolve(self, handle: EvidenceHandle) -> str: ...


class VerifierProtocol(Protocol):
    async def verify(self, claim: Claim) -> Verdict: ...


class TrustMatrixProtocol(Protocol):
    def score_each(
        self,
        draft: DraftClaim,
        *,
        source_age_days: int = 0,
    ) -> Claim: ...


class LLMProviderProtocol(Protocol):
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse: ...

    def count_tokens(self, text: str) -> int: ...


class EmbeddingsProviderProtocol(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    """Everything the agent's nodes need, assembled once per query."""

    graph_repo: GraphRepoProtocol
    embeddings_repo: EmbeddingsRepoProtocol
    snapshot_repo: SnapshotRepoProtocol
    resolver: HandleResolverProtocol
    verifier: VerifierProtocol
    trust_matrix: TrustMatrixProtocol
    llm: LLMProviderProtocol
    embeddings: EmbeddingsProviderProtocol


def build_agent_dependencies(
    *,
    session: AsyncSession,
    sourcebot: SourcebotClient,
    llm: LLMProvider,
    embeddings: EmbeddingsProvider,
) -> AgentDependencies:
    """Build the per-request dependency bundle for the agent graph.

    The agent process owns long-lived providers such as Sourcebot, LLM, and
    embeddings. Each HTTP request owns one SQLAlchemy session. This composer
    joins those process-scoped and request-scoped pieces into the repositories
    and services the LangGraph nodes consume.
    """
    graph_repo = GraphRepository(session)
    embeddings_repo = EmbeddingsRepository(session)
    snapshot_repo = SnapshotRepository(session)

    strategies: dict[HandleType, HandleResolverStrategyProtocol] = {
        HandleType.CODE_SPAN: CodeSpanResolver(sourcebot=sourcebot),
        HandleType.DOC_PARAGRAPH: DocParagraphResolver(sourcebot=sourcebot),
        HandleType.GRAPH_QUERY: GraphQueryResolver(graph_repo=graph_repo),
        HandleType.TRACE_SPAN: StubResolver(handle_type=HandleType.TRACE_SPAN),
        HandleType.DB_RECORD: StubResolver(handle_type=HandleType.DB_RECORD),
    }
    resolver = HandleResolverService(strategies=strategies)
    verifier = VerifierService(resolver=resolver)
    trust_matrix = TrustMatrixService()

    return AgentDependencies(
        graph_repo=graph_repo,
        embeddings_repo=embeddings_repo,
        snapshot_repo=snapshot_repo,
        resolver=resolver,
        verifier=verifier,
        trust_matrix=trust_matrix,
        llm=llm,
        embeddings=embeddings,
    )
