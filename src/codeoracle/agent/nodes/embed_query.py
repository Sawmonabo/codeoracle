"""
The embed_query node — knn retrieval → DOC_PARAGRAPH / CODE_SPAN citations.
"""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import (
    SPAN_EMBED_QUERY,
    NodeUpdate,
    agent_node,
    resolve_to_citation,
)
from codeoracle.agent.state import AgentState
from codeoracle.models import DraftCitation, EvidenceHandle, HandleType
from codeoracle.services.embedding.chunker import is_doc_path

# Number of nearest chunks to retrieve. Small enough to keep the compose prompt
# inside the LLM context budget, large enough to surface the symbol's own
# definition plus its immediate neighbours. Spec §4.2's *original* sketch wrote
# k=5; it was reconciled to k=4 in #44, so spec and slice now agree (Deviation
# 10). KNN_K stays independently tunable for the prompt budget — a slice-tuning
# call for prompt size.
KNN_K = 4


@agent_node(SPAN_EMBED_QUERY)
async def embed_query_node(
    state: AgentState, deps: AgentDependencies
) -> NodeUpdate:
    """Embed the symbol FQN, knn-search the snapshot's chunk embeddings, and
    turn each hit into a resolved citation — DOC_PARAGRAPH for doc paths,
    CODE_SPAN for everything else.

    Routing reuses Plan 6's `is_doc_path` predicate, which consults the same
    ``_DOC_EXTENSIONS`` set ``chunker_for`` used at index time, so a chunk
    written by ``ParagraphChunker`` is cited as DOC_PARAGRAPH and a chunk
    written by ``LineChunker`` is cited as CODE_SPAN — no drift between
    "what got chunked as a doc" and "what gets cited as a doc."

    The symbol's own definition is located by embedding proximity to the FQN
    string (Deviation 6). Each hit becomes a ``file://path:start-end`` handle
    that is resolved so the excerpt is platform-owned (invariant 1). Both
    DOC_PARAGRAPH and CODE_SPAN locators use the same ``file://`` shape
    (Plan 8: ``DocParagraphResolver`` and ``CodeSpanResolver`` share the
    ``parse_file_locator`` parser — the handle type differentiates trust
    score, not the wire path). Zero hits yields an empty list — not a failure.
    """
    sha = state["snapshot_sha"]
    vectors = await deps.embeddings.embed([state["query"].symbol])
    hits = await deps.embeddings_repo.knn(
        query_embedding=vectors[0],
        snapshot_sha=sha,
        k=KNN_K,
    )

    citations: list[DraftCitation] = []
    for hit in hits:
        handle_type = (
            HandleType.DOC_PARAGRAPH
            if is_doc_path(hit.source_path)
            else HandleType.CODE_SPAN
        )
        handle = EvidenceHandle(
            type=handle_type,
            locator=(
                f"file://{hit.source_path}:"
                f"{hit.chunk_start_line}-{hit.chunk_end_line}"
            ),
            snapshot_sha=sha,
        )
        citations.append(await resolve_to_citation(handle, deps.resolver))
    return {"embed_citations": citations}
