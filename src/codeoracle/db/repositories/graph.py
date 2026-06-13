"""GraphRepository — AGE Cypher executor.

The platform's only path to AGE goes through this class. The AGE
per-connection contract (LOAD 'age' + SET search_path) is already
enforced by db/session.py's event listener (Plan 3) — so every Cypher
call from this repository assumes AGE is loaded on the active connection.

Three operations Plan 5 needs:

  1. execute_cypher(stmt, params)
        Generic — runs a single Cypher statement; returns rows as
        list[dict[str, object]]. Used by Plan 8's GraphQueryResolver
        and Plan 10's agent graph_query node.

  2. replace_file_subgraph(snapshot_sha, file_path, cypher_inserts)
        Atomic per-file mutation per spec §8.4: DELETE the file's
        old subgraph, INSERT the new one, all in one transaction.
        File identity is the (snapshot_sha, file_path) tuple — nodes
        and edges carry these as properties.

  3. find_callers(symbol_fqn, snapshot_sha)
        Returns the files that DEFINE a symbol whose CALLS edge targets
        the given symbol_fqn at the given snapshot (spec §3.7: CALLS is
        Symbol→Symbol, File DEFINES Symbol). Backs Plan 10's agent
        `find_callers` tool.

  4. link_cross_file_symbols(snapshot_sha)
        Post-extraction pass (spec §3.7): redirect each CALLS edge whose
        callee is an unresolved (file_path=NULL) :Symbol to the resolved
        :Symbol sharing its fqn, then delete the consumed placeholder.
        Invoked once per snapshot by IndexingService after the per-file
        barrier drains, inside the advisory lock. Idempotent.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.core.exceptions import GraphQueryError

# AGE Cypher invocation shape:
# SELECT * FROM cypher('codeoracle', $$ MATCH (n) ... RETURN n $$) AS (n
# agtype);
# Parameter substitution into Cypher uses AGE's three-argument form:
# cypher('graph', $$body$$, params_map). SQLAlchemy binds one JSON string for
# the map, Postgres casts it to agtype, and AGE resolves `$param` tokens inside
# the dollar-quoted Cypher body.

_GRAPH_NAME = (
    "codeoracle"  # matches bootstrap_db.sql's create_graph('codeoracle')
)


def _serialize_age_params(params: dict[str, Any]) -> str:
    try:
        return json.dumps(params, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise GraphQueryError(
            "Cypher params must be JSON-serializable so they can be "
            "bound through AGE's agtype parameter map"
        ) from exc


class GraphRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def execute_cypher(
        self,
        cypher_body: str,
        params: dict[str, Any] | None = None,
        *,
        column_spec: str = "result agtype",
    ) -> list[dict[str, object]]:
        """Run one Cypher statement; return result rows.

        `cypher_body` is the inside of cypher('codeoracle', $$...$$) — the
        caller writes pure Cypher, not the SQL wrapper.
        `column_spec` defines the AS clause (default 'result agtype').
        `params` are passed as a Postgres parameters map to AGE.

        Plan 5 callers don't need fancy result decoding — AGE returns
        agtype values as Python strings (no psycopg agtype codec is
        registered; Plan 3's listener only runs `LOAD 'age'` + `SET
        search_path`). Callers that need the decoded form use the
        defensive `str(value).strip('"')` pattern at the call site.
        """
        if params:
            stmt_sql = (
                f"SELECT * FROM cypher('{_GRAPH_NAME}', "
                f"$cypher${cypher_body}$cypher$, "
                "CAST(:age_params AS agtype)) "
                f"AS ({column_spec})"
            )
            bind_params = {"age_params": _serialize_age_params(params)}
        else:
            stmt_sql = (
                f"SELECT * FROM cypher('{_GRAPH_NAME}', "
                f"$cypher${cypher_body}$cypher$) AS ({column_spec})"
            )
            bind_params: dict[str, object] = {}
        try:
            result = await self._session.execute(text(stmt_sql), bind_params)
            rows = result.mappings().all()
        except Exception as exc:
            raise GraphQueryError(
                f"Cypher execution failed: {exc} | stmt={cypher_body[:200]!r}"
            ) from exc
        return [dict(r) for r in rows]

    async def replace_file_subgraph(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        cypher_inserts: list[str],
    ) -> None:
        """Atomically replace the subgraph for one (snapshot_sha, file_path).

        Transaction wrapping is provided by the surrounding AsyncSession;
        callers run this inside `async with session.begin():` (or rely on
        the implicit transaction the session manages).

        cypher_inserts is a list of Cypher statements ALREADY formatted
        with the snapshot_sha + file_path properties — the JoernCpgParser
        (Plan 5 Task 12) emits them. This method DELETE-by-properties
        first, then runs each insert in sequence.
        """
        delete_stmt = (
            "MATCH (n) WHERE n.snapshot_sha = $snapshot_sha "
            "AND n.file_path = $file_path "
            "DETACH DELETE n"
        )
        await self.execute_cypher(
            delete_stmt,
            {"snapshot_sha": snapshot_sha, "file_path": file_path},
        )
        for stmt in cypher_inserts:
            await self.execute_cypher(stmt)

    async def find_callers(
        self,
        *,
        symbol_fqn: str,
        snapshot_sha: str,
    ) -> list[str]:
        """Return file paths that contain a CALLS edge into `symbol_fqn`
        at the given snapshot. Per spec §3.7, CALLS is Symbol→Symbol and a
        File DEFINES a Symbol, so a "caller file" is the file that DEFINES
        a symbol which CALLS `symbol_fqn`. The agent-orchestration
        `find_callers` tool (Plan 10) dispatches to this primitive.
        """
        # File path accessor is `file_path` (the parser's per-file partition
        # key, shared with replace_file_subgraph's DELETE) — File has no bare
        # `path` property; `AS path` is only the output-column alias.
        cypher = (
            "MATCH (caller:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->"
            "(s:Symbol {fqn:$symbol_fqn, snapshot_sha:$snapshot_sha}) "
            "MATCH (f:File {snapshot_sha:$snapshot_sha})-[:DEFINES]->(caller) "
            "RETURN DISTINCT f.file_path AS path"
        )
        rows = await self.execute_cypher(
            cypher,
            {"snapshot_sha": snapshot_sha, "symbol_fqn": symbol_fqn},
            column_spec="path agtype",
        )
        # AGE returns agtype values as quoted JSON strings; decode the
        # outer quotes that agtype adds.
        return [str(r["path"]).strip('"') for r in rows]

    async def link_cross_file_symbols(self, snapshot_sha: str) -> None:
        """Resolve cross-file CALLS edges by FQN match across one snapshot.

        Per spec §3.7: per-file CPGs cannot resolve a CALLS whose callee
        lives in another file (the callee :Symbol is provisional, with
        file_path=NULL). This pass redirects each such edge to the
        resolved :Symbol (file_path IS NOT NULL) sharing the callee fqn,
        then deletes the consumed placeholder. Provisional callees with no
        in-snapshot match (stdlib / third-party / dynamic dispatch) are
        RETAINED with file_path=NULL so consumers can tell external from
        internal symbols. Idempotent — a second run is a no-op.
        """
        await self.execute_cypher(
            "MATCH (caller:Symbol {snapshot_sha:$sha})-[r:CALLS]->"
            "(unresolved:Symbol {snapshot_sha:$sha}) "
            "WHERE unresolved.file_path IS NULL "
            "MATCH (resolved:Symbol {fqn:unresolved.fqn, snapshot_sha:$sha}) "
            "WHERE resolved.file_path IS NOT NULL "
            "MERGE (caller)-[:CALLS]->(resolved) "
            "DELETE r, unresolved",
            {"sha": snapshot_sha},
        )
