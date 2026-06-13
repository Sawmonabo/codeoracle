"""
Plan 10 Task 6 — the embed_query node: knn retrieval → DOC_PARAGRAPH /
CODE_SPAN citations.
"""

import pytest

from codeoracle.agent.nodes.embed_query import KNN_K, embed_query_node
from codeoracle.models import EmbeddingHit, HandleType
from codeoracle.services.handle_resolution.strategies.base import (
    parse_file_locator,
)


async def test_embed_query_builds_code_span_citations(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(resolve_content="SRC")
    update = await embed_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    citations = update["embed_citations"]
    assert isinstance(citations, list)
    assert len(citations) == 3
    # the default fake supplies 3 hits (all .py paths)
    for c in citations:
        assert c.handle.type == HandleType.CODE_SPAN
        assert c.handle.locator.startswith("file://")
        assert c.excerpt.startswith("SRC::file://")  # invariant 1


async def test_embed_query_locator_encodes_the_hit_line_range(
    make_fake_deps, make_agent_state
) -> None:
    hit = EmbeddingHit(
        snapshot_sha="abc1234",
        source_path="src/codeoracle/services/verifier/service.py",
        chunk_start_line=42,
        chunk_end_line=58,
        content="...",
        distance=0.05,
    )
    deps = make_fake_deps(knn_hits=[hit])
    update = await embed_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    citations = update["embed_citations"]
    assert isinstance(citations, list)
    locator = citations[0].handle.locator
    assert (
        locator == "file://src/codeoracle/services/verifier/service.py:42-58"
    )


async def test_embed_query_locator_round_trips_through_plan8_parser(
    make_fake_deps, make_agent_state
) -> None:
    """The ``file://`` locator ``embed_query`` builds from an ``EmbeddingHit``
    is parsed back by Plan 8's REAL ``parse_file_locator`` into the exact
    ``source_path`` + line range the hit carried (D-10.U9).

    This pins the cross-plan locator-format contract end-to-end: Plan 6 WRITES
    ``EmbeddingHit.source_path`` as a repo-root-relative POSIX path (the embed
    worker sets ``source_path = message.file_path``, a path relative to
    ``repo_root``), ``embed_query`` (the READER) interpolates
    it into ``file://{source_path}:{start}-{end}``, and Plan 8's
    ``CodeSpanResolver`` / ``DocParagraphResolver`` resolve it via the shared
    ``parse_file_locator``. Driving the parser with the locator ``embed_query``
    *actually emits* (not a hand-built literal) is what catches format drift —
    a relative-vs-absolute change, a stray snapshot prefix, or a separator swap
    would make the round-trip lose the path or the range and silently miss
    CODE_SPAN resolution at query time.

    The ``+wt<12hex>`` dirty-tree suffix lives on ``snapshot_sha`` (stripped at
    the resolver layer by ``head_sha_only``, Plan 8 Invariant 10), never on the
    locator path — so it is correctly out of scope for this path-format
    contract.
    """
    hit = EmbeddingHit(
        snapshot_sha="abc1234",
        source_path="src/codeoracle/services/verifier/service.py",
        chunk_start_line=42,
        chunk_end_line=58,
        content="...",
        distance=0.05,
    )
    deps = make_fake_deps(knn_hits=[hit])
    update = await embed_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    citations = update["embed_citations"]
    assert isinstance(citations, list)
    locator = citations[0].handle.locator

    path, (start, end) = parse_file_locator(locator)
    # Plan 8 preserves the path verbatim (no normalization) and the range is
    # 1-indexed inclusive — both sides must agree on the relative-POSIX form.
    assert path == hit.source_path
    assert (start, end) == (hit.chunk_start_line, hit.chunk_end_line)


async def test_embed_query_caps_results_at_knn_k(
    make_fake_deps, make_agent_state
) -> None:
    many = [
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path=f"src/f{i}.py",
            chunk_start_line=1,
            chunk_end_line=9,
            content="x",
            distance=0.1 * i,
        )
        for i in range(KNN_K + 6)
    ]
    deps = make_fake_deps(knn_hits=many)
    update = await embed_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    citations = update["embed_citations"]
    assert isinstance(citations, list)
    assert len(citations) == KNN_K


async def test_embed_query_handles_zero_hits(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(knn_hits=[])
    update = await embed_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    assert update["embed_citations"] == []


# --- DOC_PARAGRAPH / CODE_SPAN routing (Plan 6 `is_doc_path`) ----------------


@pytest.mark.parametrize(
    ("source_path", "expected_type"),
    [
        ("docs/architecture.md", HandleType.DOC_PARAGRAPH),
        ("docs/specs/intro.rst", HandleType.DOC_PARAGRAPH),
        ("api.adoc", HandleType.DOC_PARAGRAPH),
        ("README.markdown", HandleType.DOC_PARAGRAPH),
        ("CHANGELOG.MD", HandleType.DOC_PARAGRAPH),  # case-insensitive
        ("src/codeoracle/agent/nodes/embed_query.py", HandleType.CODE_SPAN),
        ("types.pyi", HandleType.CODE_SPAN),
        (
            "notes.txt",
            HandleType.CODE_SPAN,
        ),  # unknown extension → conservative CODE_SPAN
        ("LICENSE", HandleType.CODE_SPAN),  # no extension → CODE_SPAN
        ("Dockerfile", HandleType.CODE_SPAN),
    ],
)
async def test_embed_query_routes_handle_type_by_source_path_extension(
    make_fake_deps,
    make_agent_state,
    source_path: str,
    expected_type: HandleType,
) -> None:
    """`embed_query` routes each EmbeddingHit to DOC_PARAGRAPH (doc extension)
    or CODE_SPAN (everything else) via Plan 6's `is_doc_path`. This pins the
    cross-plan routing contract — every chunk written by ParagraphChunker
    must cite as DOC_PARAGRAPH and every chunk written by LineChunker must
    cite as CODE_SPAN (Plan 6 Architectural Invariant 5)."""
    hit = EmbeddingHit(
        snapshot_sha="abc1234",
        source_path=source_path,
        chunk_start_line=1,
        chunk_end_line=12,
        content="...",
        distance=0.1,
    )
    deps = make_fake_deps(knn_hits=[hit])
    update = await embed_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    citations = update["embed_citations"]
    assert isinstance(citations, list)
    citation = citations[0]
    assert citation.handle.type is expected_type
    # Locator shape is identical for both kinds (Plan 8: same
    # parse_file_locator).
    assert citation.handle.locator == f"file://{source_path}:1-12"


async def test_embed_query_emits_both_handle_types_in_one_batch(
    make_fake_deps, make_agent_state
) -> None:
    """A mixed-extension knn result yields a citation list with BOTH
    DOC_PARAGRAPH and CODE_SPAN entries, in the same order as the hits.
    Catches a regression that would route every hit to one kind regardless
    of source_path (e.g., an accidental hard-coded CODE_SPAN)."""
    hits = [
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="docs/architecture.md",
            chunk_start_line=1,
            chunk_end_line=5,
            content="md1",
            distance=0.10,
        ),
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="src/codeoracle/agent/runtime.py",
            chunk_start_line=10,
            chunk_end_line=25,
            content="py1",
            distance=0.15,
        ),
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="docs/specs/scaffolding.rst",
            chunk_start_line=1,
            chunk_end_line=8,
            content="rst1",
            distance=0.20,
        ),
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="src/codeoracle/services/verifier/service.py",
            chunk_start_line=40,
            chunk_end_line=58,
            content="py2",
            distance=0.25,
        ),
    ]
    deps = make_fake_deps(knn_hits=hits)
    update = await embed_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    citations = update["embed_citations"]
    assert isinstance(citations, list)
    assert [c.handle.type for c in citations] == [
        HandleType.DOC_PARAGRAPH,
        HandleType.CODE_SPAN,
        HandleType.DOC_PARAGRAPH,
        HandleType.CODE_SPAN,
    ]
