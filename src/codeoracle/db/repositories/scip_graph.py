"""ScipGraphRepository — `:Definition` + `:Reference` AGE writes.

Plan 6. The writer is `workers/scip_indexer` (project-mode worker). Reads are
done by Plan 9's verifier directly via openCypher queries.

Label namespace: `:Definition` and `:Reference`. These are disjoint from
Plan 5's Joern-side `:Symbol` namespace (architectural invariant 4). The
verifier in Plan 9 correlates `:Symbol{fqn}` (Joern) with `:Definition{fqn}`
(SCIP) by FQN equality — same value of `fqn` across labels indicates the
same symbol from two evidence sources.

Atomicity: `replace_file_subgraph` deletes ALL `:Definition`/`:Reference`
nodes for (snapshot_sha, file_path), then inserts the provided sets. The
session is NOT committed here — the worker commits per-file.

============================================================================
AGE EXECUTION + PARAMETER-BINDING NOTE (read before editing):
============================================================================

This repository COMPOSES Plan 5's `GraphRepository` (at
`codeoracle.db.repositories.graph`). All Cypher execution flows through
`GraphRepository.execute_cypher(cypher_body, *, column_spec=...)`, which wraps
the body in `SELECT * FROM cypher('codeoracle', $$...$$, params) AS (...)`,
passes params via AGE's agtype map, and translates AGE errors to
`GraphQueryError`. There is NO separate
`codeoracle.db.age_driver` module — that name was a stale draft (P0-13 /
D-6.8).

This repository uses VALIDATE-THEN-BIND: each value passed into Cypher is first
validated against a strict allowlist/range check (`_SAFE_SHA_RE`,
`_SAFE_PATH_RE`, `_SAFE_FQN_RE`, `_safe_line`), then bound through
`GraphRepository.execute_cypher` as `$snapshot_sha`, `$file_path`, `$fqn`,
`$line_start`, and `$line_end`.

This pattern is safe BECAUSE all values originate from inside CodeOracle:
  - `snapshot_sha` comes from `IndexingService` (git SHA, already validated)
  - `file_path` comes from the snapshot manifest (path under repo_root)
  - `fqn` comes from `scip-python` output (validated by the parser)
  - line numbers are ints already

The validator regexes are deliberately tight — anything that doesn't match
raises `ValueError` rather than silently passing garbage to Cypher.
============================================================================
"""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.scip.parser import ScipDefinition, ScipReference
from codeoracle.db.repositories.graph import GraphRepository

# Validation regexes for the validate-then-bind Cypher-injection escape
# strategy. See the module docstring "AGE PARAMETER-BINDING NOTE" for why.
_SAFE_SHA_RE = re.compile(r"^[a-fA-F0-9_-]{1,128}$")  # git SHA or test sha
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,512}$")  # repo-relative paths
# FQN can contain language-syntax punctuation. Allow common scip-python forms:
# `module.py`/Class#method(). + Java-ish forms with $ and (. Forbid quote chars
# (', ") and Cypher meta (}, {) so injection into property literal is
# impossible.
_SAFE_FQN_RE = re.compile(r"^[A-Za-z0-9_./()$#\[\]@%`<>,:\s-]{1,1024}$")
_MAX_SAFE_LINE = 10_000_000


def _safe_sha(s: str) -> str:
    if not _SAFE_SHA_RE.match(s):
        raise ValueError(f"unsafe snapshot_sha value for AGE Cypher: {s!r}")
    return s


def _safe_path(p: str) -> str:
    if not _SAFE_PATH_RE.match(p):
        raise ValueError(f"unsafe file_path value for AGE Cypher: {p!r}")
    return p


def _safe_fqn(f: str) -> str:
    if not _SAFE_FQN_RE.match(f):
        raise ValueError(f"unsafe fqn value for AGE Cypher: {f!r}")
    return f


def _safe_line(n: int) -> int:
    if not isinstance(n, int) or n < 0 or n > _MAX_SAFE_LINE:
        raise ValueError(f"unsafe line number for AGE Cypher: {n!r}")
    return n


class ScipGraphRepository:
    """SCIP-side façade over Plan 5's `GraphRepository`.

    Composes (does not extend) `GraphRepository` — the caller passes a
    session; this class constructs its own `GraphRepository(session)` and
    delegates all Cypher execution to `execute_cypher`. The session is
    owned by the caller; both repositories share it so a single transaction
    wraps SCIP writes + the worker's barrier upsert.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._graph = GraphRepository(session)

    async def replace_file_subgraph(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        definitions: list[ScipDefinition],
        references: list[ScipReference],
    ) -> None:
        """Atomically replace all SCIP nodes for (snapshot_sha, file_path).

        Empty `definitions`/`references` lists are legal (pure delete).
        """
        sha_s = _safe_sha(snapshot_sha)
        path_s = _safe_path(file_path)

        # Delete existing nodes for this (snapshot, file). Note AGE Cypher
        # does NOT support `OR` between label tests in the WHERE clause the
        # way openCypher does (`n:Definition OR n:Reference`). We issue two
        # separate DETACH DELETE statements instead.
        await self._graph.execute_cypher(
            "MATCH (n:Definition {snapshot_sha:$snapshot_sha, "
            "file_path:$file_path}) DETACH DELETE n",
            {"snapshot_sha": sha_s, "file_path": path_s},
        )
        await self._graph.execute_cypher(
            "MATCH (n:Reference {snapshot_sha:$snapshot_sha, "
            "file_path:$file_path}) DETACH DELETE n",
            {"snapshot_sha": sha_s, "file_path": path_s},
        )

        # Insert new definitions.
        #
        # IMPORTANT: we store `path_s` (the outer file_path argument, which
        # came from message.files — canonical relative paths) on EVERY node,
        # NOT `d.file_path` from the ScipDefinition record. SCIP-python may
        # emit absolute paths or paths prefixed with `src/codeoracle/` etc.,
        # and
        # `tasks.py` already grouped definitions by their normalized path
        # via _normalize_scip_path_to_message_file. The grouping contract
        # is "all elements in this list belong to file_path" — so reusing
        # `path_s` is correct and keeps the storage key consistent with the
        # DELETE above and the count_definitions() lookup below. Re-validating
        # `d.file_path` per element here would (a) re-introduce the mismatch
        # the normalizer was added to fix, and (b) reject SCIP-emitted paths
        # whose shape doesn't match _SAFE_PATH_RE.
        for d in definitions:
            fqn_s = _safe_fqn(d.fqn)
            line_start = _safe_line(d.line_number)
            line_end = _safe_line(d.end_line)
            await self._graph.execute_cypher(
                """
                CREATE (n:Definition {{
                    fqn: $fqn,
                    snapshot_sha: $snapshot_sha,
                    file_path: $file_path,
                    line_start: $line_start,
                    line_end: $line_end
                }})
                """,
                {
                    "fqn": fqn_s,
                    "snapshot_sha": sha_s,
                    "file_path": path_s,
                    "line_start": line_start,
                    "line_end": line_end,
                },
            )

        # Insert new references — same path-canonicalization rationale as
        # the definitions block above; use `path_s`, not `r.file_path`.
        for r in references:
            fqn_s = _safe_fqn(r.fqn)
            line_start = _safe_line(r.line_number)
            line_end = _safe_line(r.end_line)
            await self._graph.execute_cypher(
                """
                CREATE (n:Reference {{
                    fqn: $fqn,
                    snapshot_sha: $snapshot_sha,
                    file_path: $file_path,
                    line_start: $line_start,
                    line_end: $line_end
                }})
                """,
                {
                    "fqn": fqn_s,
                    "snapshot_sha": sha_s,
                    "file_path": path_s,
                    "line_start": line_start,
                    "line_end": line_end,
                },
            )

    async def count_definitions(
        self, *, snapshot_sha: str, file_path: str
    ) -> int:
        sha_s = _safe_sha(snapshot_sha)
        path_s = _safe_path(file_path)
        rows = await self._graph.execute_cypher(
            "MATCH (n:Definition {snapshot_sha:$snapshot_sha, "
            "file_path:$file_path}) RETURN count(n) AS c",
            {"snapshot_sha": sha_s, "file_path": path_s},
            column_spec="c agtype",
        )
        # AGE returns the agtype-encoded count as a string; the
        # str(...).strip('"') is defensive against both `5` and `"5"` shapes
        # — same pattern as Plan 5's GraphRepository.find_callers.
        return int(str(rows[0]["c"]).strip('"'))

    async def count_references(
        self, *, snapshot_sha: str, file_path: str
    ) -> int:
        sha_s = _safe_sha(snapshot_sha)
        path_s = _safe_path(file_path)
        rows = await self._graph.execute_cypher(
            "MATCH (n:Reference {snapshot_sha:$snapshot_sha, "
            "file_path:$file_path}) RETURN count(n) AS c",
            {"snapshot_sha": sha_s, "file_path": path_s},
            column_spec="c agtype",
        )
        return int(str(rows[0]["c"]).strip('"'))
