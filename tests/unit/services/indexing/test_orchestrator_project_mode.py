"""Project-mode orchestrator behavior.

These tests do NOT exercise a real SCIP worker — they pin the orchestrator's
enqueue shape (one message per project-mode extractor, N messages per
per_file-mode extractor) and the barrier's counting behavior when a single
worker writes N outcomes from one message.

Fixtures (Plan 3 + Plan 5):
- `async_session_factory` — yields `async_sessionmaker[AsyncSession]` bound to
  the testcontainers PG instance.
- `valkey_queue_client` — Plan 5 ValkeyQueueClient bound to the testcontainers
  Valkey instance.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio

import codeoracle.services.indexing.orchestrator as orchestrator_mod
from codeoracle.core import streams
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator


@dataclass
class _QueuedMessage:
    payload: dict[str, object]


@dataclass
class _MemoryQueue:
    messages: dict[str, list[dict[str, object]]] = field(default_factory=dict)

    async def enqueue(self, stream: str, payload: dict[str, object]) -> str:
        self.messages.setdefault(stream, []).append(payload)
        return f"{len(self.messages[stream])}-0"

    async def stream_length(self, stream: str) -> int:
        return len(self.messages.get(stream, []))

    async def read_pending(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[_QueuedMessage]:
        _ = (group, consumer, block_ms)
        return [
            _QueuedMessage(payload=payload)
            for payload in self.messages.get(stream, [])[:count]
        ]


class _FakeSession:
    def __init__(self, counts: dict[tuple[str, str], tuple[int, int]]) -> None:
        self.counts = counts


class _FakeSessionContext:
    def __init__(self, counts: dict[tuple[str, str], tuple[int, int]]) -> None:
        self._counts = counts

    async def __aenter__(self) -> _FakeSession:
        return _FakeSession(self._counts)

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, counts: dict[tuple[str, str], tuple[int, int]]) -> None:
        self._counts = counts

    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext(self._counts)


class _FakeOutcomeRepository:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def count_completed(
        self, *, snapshot_sha: str, extractor: str
    ) -> int:
        completed, _failed = self._session.counts.get(
            (snapshot_sha, extractor), (0, 0)
        )
        return completed

    async def count_failed(self, *, snapshot_sha: str, extractor: str) -> int:
        _completed, failed = self._session.counts.get(
            (snapshot_sha, extractor), (0, 0)
        )
        return failed


@pytest.fixture
def outcome_counts() -> dict[tuple[str, str], tuple[int, int]]:
    return {}


@pytest.fixture(autouse=True)
def _patch_outcomes_repo(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator_mod,
        "SnapshotFileOutcomeRepository",
        _FakeOutcomeRepository,
    )


@pytest.fixture
def valkey_queue_client() -> _MemoryQueue:
    return _MemoryQueue()


@pytest.fixture
def async_session_factory(
    outcome_counts: dict[tuple[str, str], tuple[int, int]],
) -> _FakeSessionFactory:
    return _FakeSessionFactory(outcome_counts)


@pytest_asyncio.fixture
async def orchestrator(
    valkey_queue_client: _MemoryQueue,
    async_session_factory: _FakeSessionFactory,
) -> AsyncIterator[IndexingOrchestrator]:
    orch = IndexingOrchestrator(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        poll_interval_seconds=0.1,
    )
    yield orch


@pytest.mark.asyncio
async def test_per_file_extractor_enqueues_n_messages(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: _MemoryQueue,
) -> None:
    """Per-file extractor (joern, opengrep, embed): N files → N messages."""
    await orchestrator._enqueue_all(
        snapshot_sha="sha-test-perfile",
        repo_root="/tmp/fake-repo",
        files=["a.py", "b.py", "c.py"],
        extractor_kinds=("joern",),
    )

    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_JOERN)
        == 3
    )


@pytest.mark.asyncio
async def test_project_mode_extractor_enqueues_one_message(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: _MemoryQueue,
) -> None:
    """
    Project-mode extractor (scip): N files → 1 message containing all files.
    """
    await orchestrator._enqueue_all(
        snapshot_sha="sha-test-project",
        repo_root="/tmp/fake-repo",
        files=["a.py", "b.py", "c.py"],
        extractor_kinds=("scip",),
    )

    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_SCIP)
        == 1
    )

    messages = await valkey_queue_client.read_pending(
        stream=streams.STREAM_INDEXING_SCIP,
        group="g_test_project_mode",
        consumer="c",
        count=1,
        block_ms=500,
    )
    msg_payload = messages[0].payload
    assert msg_payload["snapshot_sha"] == "sha-test-project"
    assert msg_payload["batch_id"] == "sha-test-project"
    assert msg_payload["repo_root"] == "/tmp/fake-repo"
    assert msg_payload["files"] == ["a.py", "b.py", "c.py"]


@pytest.mark.asyncio
async def test_mixed_kinds_enqueue_correctly(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: _MemoryQueue,
) -> None:
    """
    Mixed: per_file + project + per_file enqueue independently on their
    streams.
    """
    await orchestrator._enqueue_all(
        snapshot_sha="sha-mixed",
        repo_root="/tmp/fake-repo",
        files=["a.py", "b.py"],
        extractor_kinds=("joern", "scip", "opengrep", "embed"),
    )

    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_JOERN)
        == 2
    )
    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_SCIP)
        == 1
    )
    assert (
        await valkey_queue_client.stream_length(
            streams.STREAM_INDEXING_OPENGREP
        )
        == 2
    )
    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_EMBED)
        == 2
    )


@pytest.mark.asyncio
async def test_barrier_closes_when_project_worker_writes_n_outcomes(
    orchestrator: IndexingOrchestrator,
    outcome_counts: dict[tuple[str, str], tuple[int, int]],
) -> None:
    """Project-mode barrier semantics: orchestrator polls outcome count by
    (snapshot_sha, extractor), not by message count. A worker that writes
    N outcomes from 1 message closes the barrier exactly when N rows exist."""
    snapshot_sha = "sha-barrier"
    files = ["x.py", "y.py", "z.py"]

    # Simulate the SCIP worker writing 3 outcomes from one consumed message.
    outcome_counts[(snapshot_sha, "scip")] = (3, 0)

    # The orchestrator's _is_barrier_closed for ("scip",) and 3 files: true.
    barrier_closed = orchestrator._is_barrier_closed
    closed = await barrier_closed(
        snapshot_sha=snapshot_sha,
        files=files,
        extractor_kinds=("scip",),
    )
    assert closed


# ---- G14: per-extractor file routing tests --------------------------------


@pytest.mark.asyncio
async def test_g14_code_extractor_filters_out_doc_files(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: _MemoryQueue,
) -> None:
    """
    joern accepts only .py / .pyi — .md files must NOT reach the joern stream.
    """
    await orchestrator._enqueue_all(
        snapshot_sha="sha-g14-code-filter",
        repo_root="/tmp/fake-repo",
        files=["a.py", "README.md", "docs/intro.md", "b.py"],
        extractor_kinds=("joern",),
    )

    # Only a.py + b.py routed to joern.
    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_JOERN)
        == 2
    )


@pytest.mark.asyncio
async def test_g14_embed_extractor_accepts_all_files(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: _MemoryQueue,
) -> None:
    """
    embed has accepted_extensions=None — all files reach the embed stream.
    """
    await orchestrator._enqueue_all(
        snapshot_sha="sha-g14-embed-all",
        repo_root="/tmp/fake-repo",
        files=["a.py", "README.md", "docs/intro.md", "b.py"],
        extractor_kinds=("embed",),
    )

    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_EMBED)
        == 4
    )  # all four files


@pytest.mark.asyncio
async def test_g14_scip_project_message_carries_only_code_files(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: _MemoryQueue,
) -> None:
    """SCIP (project-mode + code-only filter): the single project message's
    `files` list contains only routed code files; doc files are excluded."""
    await orchestrator._enqueue_all(
        snapshot_sha="sha-g14-scip",
        repo_root="/tmp/fake-repo",
        files=["a.py", "README.md", "b.py", "CHANGELOG.md"],
        extractor_kinds=("scip",),
    )

    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_SCIP)
        == 1
    )

    messages = await valkey_queue_client.read_pending(
        stream=streams.STREAM_INDEXING_SCIP,
        group="g_test_scip_code_files",
        consumer="c",
        count=1,
        block_ms=500,
    )
    payload = messages[0].payload
    assert payload["files"] == [
        "a.py",
        "b.py",
    ]  # README/CHANGELOG filtered out


@pytest.mark.asyncio
async def test_g14_all_doc_snapshot_skips_code_extractor_enqueue(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: _MemoryQueue,
) -> None:
    """When the snapshot has zero code files, code-extractor streams stay empty
    (no zero-files project message, no per-file messages)."""
    await orchestrator._enqueue_all(
        snapshot_sha="sha-g14-all-docs",
        repo_root="/tmp/fake-repo",
        files=["README.md", "docs/intro.md", "CHANGELOG.md"],
        extractor_kinds=("joern", "scip", "opengrep", "embed"),
    )

    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_JOERN)
        == 0
    )
    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_SCIP)
        == 0
    )
    assert (
        await valkey_queue_client.stream_length(
            streams.STREAM_INDEXING_OPENGREP
        )
        == 0
    )
    # Embed accepts all → 3 messages.
    assert (
        await valkey_queue_client.stream_length(streams.STREAM_INDEXING_EMBED)
        == 3
    )


@pytest.mark.asyncio
async def test_g14_barrier_trivially_closes_when_no_code_routed(
    orchestrator: IndexingOrchestrator,
) -> None:
    """A snapshot of only .md files passed to extractor_kinds=("joern",) closes
    the joern barrier immediately — expected=0, completed+failed=0.

    Without G14 this would have hung forever: expected was len(files) raw, so
    the barrier waited for joern outcomes on .md files that were never
    enqueued.
    """
    barrier_closed = orchestrator._is_barrier_closed
    closed = await barrier_closed(
        snapshot_sha="sha-g14-no-code",
        files=["README.md", "docs/intro.md"],
        extractor_kinds=("joern",),
    )
    assert closed


@pytest.mark.asyncio
async def test_g14_collect_outcomes_returns_success_for_zero_expected(
    orchestrator: IndexingOrchestrator,
) -> None:
    """
    _collect_outcomes returns "success" (not "failed") for an extractor whose
    filtered file set is empty — no work routed = trivially complete."""
    out = await orchestrator._collect_outcomes(
        snapshot_sha="sha-g14-collect-zero",
        files=["README.md"],
        extractor_kinds=("joern", "scip", "opengrep", "embed"),
    )
    assert out["joern"] == "success"
    assert out["scip"] == "success"
    assert out["opengrep"] == "success"
    # Embed gets the .md and writes nothing yet → barrier reports "failed"
    # because expected=1, total=0 (barrier timed out OR the worker hasn't
    # written yet). This branch is exercised by other tests; here we only
    # pin the zero-expected branch on the code extractors.


def test_g14_filter_files_for_helper_is_pure_and_classmethod() -> None:
    """`_filter_files_for` is a @classmethod (no instance state) — callable on
    the class directly. Tests AND production code both go through it."""
    filter_files = IndexingOrchestrator._filter_files_for
    routed_joern = filter_files(
        files=["a.py", "b.PY", "c.pyi", "README.md", "data.txt"],
        kind="joern",
    )
    assert routed_joern == [
        "a.py",
        "b.PY",
        "c.pyi",
    ]  # case-insensitive on suffix

    routed_embed = filter_files(
        files=["a.py", "README.md", "data.txt"],
        kind="embed",
    )
    assert routed_embed == ["a.py", "README.md", "data.txt"]
