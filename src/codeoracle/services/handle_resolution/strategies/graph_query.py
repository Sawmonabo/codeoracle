"""GraphQueryResolver — resolves cypher://<query> handles via GraphRepository.

The locator IS the Cypher query. The resolver re-runs it at verify time
and stringifies the result deterministically so the Verifier can compare
byte-exact against the LLM's claimed excerpt.

AGE returns agtype values: string scalars appear JSON-quoted (`"foo"`);
ints, floats, and bools appear bare. graph_query citations are constrained
to single-column, scalar-per-row returns (see the plan's Task 11 prose),
so this module unwraps the outer quotes on string scalars and emits a
stable JSON array. Output order is the row order alone (a flat list of
scalars), so no key sorting is needed.
"""

import json
import re
from collections.abc import Mapping, Sequence

from codeoracle.core.exceptions import GraphQueryError, ResolverError
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.models.evidence import EvidenceHandle
from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
)

_CYPHER_SCHEME = "cypher://"

# The snapshot pin must appear as a node-property-map CONSTRAINT binding the
# AGE/Postgres `$snapshot_sha` parameter — `{... snapshot_sha: $snapshot_sha
# ...}`
# — not merely as a token somewhere in the body. A token-presence test
# (`"$snapshot_sha"
# in body`) is insufficient: a body like `MATCH (c:Symbol) WHERE true OR
# c.snapshot_sha = $snapshot_sha RETURN c.fqn AS result` mentions the parameter
# yet
# reads EVERY snapshot. Requiring the property-map form matches the exact shape
# the
# agent prompt mandates (`MATCH (... {snapshot_sha:$snapshot_sha}) ...`) and
# makes the
# pin a structural filter, not a decoration. See the Task 11 "snapshot pin is
# enforced
# structurally" prose and audit finding D-8.1.
#
# The pattern tolerates arbitrary intra-brace whitespace and is applied AFTER
# comment-stripping (a `//`- or `/* */`-commented mention must not satisfy the
# check).
_SNAPSHOT_CONSTRAINT_RE = re.compile(
    r"\{[^{}]*\bsnapshot_sha\s*:\s*\$snapshot_sha\b[^{}]*\}"
)

# Strip Cypher line comments (`// ... EOL`) and block comments (`/* ... */`) so
# a
# commented-out constraint cannot satisfy `_SNAPSHOT_CONSTRAINT_RE`. Cypher
# string
# literals could in principle contain `//`, but a graph_query citation never
# embeds
# the snapshot constraint inside a string literal, so this conservative strip
# is safe
# for the slice. (A full Cypher tokenizer is out of scope — see the residual
# note in
# the Task 11 prose.)
_CYPHER_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_CYPHER_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_MIN_QUOTED_AGTYPE_STRING_LEN = 2


def _strip_cypher_comments(cypher_body: str) -> str:
    """Remove `//` line comments and `/* */` block comments from a Cypher body.

    Used only to harden `_assert_snapshot_pinned` against a commented-out
    snapshot
    constraint; the value-stripped body is NOT what gets executed (the original
    body is forwarded verbatim).
    """
    without_block = _CYPHER_BLOCK_COMMENT_RE.sub(" ", cypher_body)
    return _CYPHER_LINE_COMMENT_RE.sub(" ", without_block)


def _parse_cypher_locator(locator: str) -> str:
    """Extract the Cypher body from `cypher://<body>`.

    Raises: ValueError: if scheme is missing or body is empty.
    """
    if not locator.startswith(_CYPHER_SCHEME):
        raise ValueError(f"locator missing 'cypher://' scheme: {locator!r}")
    body = locator[len(_CYPHER_SCHEME) :]
    if not body.strip():
        raise ValueError(f"cypher locator has empty body: {locator!r}")
    return body


def _assert_snapshot_pinned(cypher_body: str, locator: str) -> None:
    """
    Reject a Cypher body that does not CONSTRAIN on the `$snapshot_sha` param.

    Snapshot isolation in the graph is a per-node `snapshot_sha` property
    (Plan 5 — every :Symbol / :File carries it). A graph_query citation MUST
    constrain on that property via a node-property-map binding the
    `$snapshot_sha` parameter, e.g.
    `MATCH (c:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->... RETURN c.fqn
    AS result`. The resolver fails CLOSED here — before execution — rather than
    trusting the agent prompt to have included the pin.

    This is a CONSTRAINT check, not a token-presence check. A body that merely
    mentions `$snapshot_sha` without binding it inside a node-property map
    (e.g. `MATCH (c:Symbol) WHERE true OR c.snapshot_sha = $snapshot_sha
    RETURN c.fqn AS result`) reads every snapshot and is REJECTED. Comments are
    stripped first so a `//`- or `/* */`-commented constraint does not satisfy
    the check.

    Residual (accepted risk): the regex guarantees >=1 matched node carries the
    constraint ("at least one pinned anchor"); it cannot prove that EVERY
    independent traversal root does. A node reachable from a pinned anchor is
    safe (it is already scoped); the residual is an ADDITIONAL independent
    MATCH/UNION arm that introduces its own unanchored root, which would pass
    this check yet read across snapshots in that arm. Closing that requires a
    Cypher AST validator, which the stack does not have; it is owned by the
    future graph-query-hardening plan (the same owner that would make
    `_unwrap_agtype_value` recurse for non-scalar agtype). The slice's agent
    prompt emits single-arm queries with a pinned root only, so the residual is
    not reachable in slice scope. This narrows — does not fully close — the
    D-8.1 injection surface; the prose above states the boundary.

    Raises: ResolverError: the Cypher body does not bind `$snapshot_sha` as a
            node-property-map constraint.
    """
    if not _SNAPSHOT_CONSTRAINT_RE.search(_strip_cypher_comments(cypher_body)):
        raise ResolverError(
            f"graph_query locator {locator!r} does not constrain on the "
            "required "
            "`$snapshot_sha` parameter via a node-property map "
            "(expected `{... snapshot_sha: $snapshot_sha ...}`); the "
            "snapshot pin must scope the query structurally so it cannot "
            "read across snapshots"
        )


def _unwrap_agtype_value(value: object) -> object:
    """Strip one outer pair of JSON quotes that AGE adds to a string scalar.

    Per Plan 5 Task 8: AGE returns `"foo"` (string with quotes), not `foo`.
    Numeric and bool scalars pass through unchanged. This helper does NOT
    recurse into maps or lists: graph_query is constrained to scalar-per-row
    returns (see Task 11 prose), so non-scalar agtype never reaches it.
    """
    if (
        isinstance(value, str)
        and len(value) >= _MIN_QUOTED_AGTYPE_STRING_LEN
        and value[0] == '"'
        and value[-1] == '"'
    ):
        return value[1:-1]
    return value


def stringify_cypher_rows(rows: Sequence[Mapping[str, object]]) -> str:
    """Convert single-column scalar rows into a deterministic JSON string.

    Each row's 'result' scalar is unwrapped (agtype-string → str), then the
    list of values is JSON-encoded with standard separators.
    """
    values = [_unwrap_agtype_value(row.get("result")) for row in rows]
    # `values` is always a flat list of scalars (graph_query is
    # scalar-per-row),
    # so list order alone fixes the output — no `sort_keys` is needed or
    # passed.
    return json.dumps(values, separators=(", ", ": "))


class GraphQueryResolver(HandleResolverStrategy):
    """Resolves GRAPH_QUERY handles by re-running the embedded Cypher and
    stringifying the result deterministically.

    The handle.snapshot_sha is enforced STRUCTURALLY, not via the prompt. The
    resolver (a) strips any `+wt<12hex>` dirty-tree suffix via `head_sha_only`
    (G21 consistency — the same normalization CodeSpanResolver / DocParagraph-
    Resolver apply before a Sourcebot call), (b) asserts the LLM-authored
    Cypher
    binds `$snapshot_sha` as a node-property-map CONSTRAINT (`_assert_snapshot_
    pinned` — a constraint check, not a mere token-presence check; comments are
    stripped first) and rejects it with ResolverError otherwise, and (c) passes
    `params={"snapshot_sha": <head sha>}` so AGE BINDS the value from the
    handle.
    The query text can therefore never substitute a different snapshot — the
    value does not come from the body — and a single-arm query that fails to
    filter on the pin is rejected before execution. This NARROWS the D-8.1
    injection surface to the structurally-enforceable cases (matching the
    structural, not prompt-only, guarantee the span / doc resolvers carry); the
    one residual a non-parser check cannot reach — an ADDITIONAL independent
    MATCH/UNION arm that introduces its own unanchored root (nodes reachable
    from
    a pinned anchor are already scoped) — is documented as accepted risk with a
    named owner (the future graph-query-hardening plan) in the
    `_assert_snapshot_pinned` docstring and the Task 11 prose. The slice agent
    emits single-arm queries with a pinned root only, so the residual is
    unreachable in slice scope.
    """

    def __init__(self, graph_repo: GraphRepository) -> None:
        self._graph_repo = graph_repo

    async def resolve(self, handle: EvidenceHandle) -> str:
        cypher_body = _parse_cypher_locator(handle.locator)
        _assert_snapshot_pinned(cypher_body, handle.locator)
        # Normalize a dirty-tree `+wt` SHA to its head before binding (G21):
        # the
        # graph indexes nodes under the head snapshot_sha, exactly as Sourcebot
        # indexes the head ref. The value is BOUND via params — never spliced
        # into the body — so the pin is structural.
        head_sha = head_sha_only(handle.snapshot_sha)
        try:
            rows = await self._graph_repo.execute_cypher(
                cypher_body,
                {"snapshot_sha": head_sha},
                column_spec="result agtype",
            )
        except GraphQueryError as e:
            # Service-boundary translation: GraphQueryError is a worker-family
            # exception (Plan 5: TransientWorkerError -> WorkerError). It MUST
            # NOT
            # escape the services/ layer in workers/ vocabulary, so it is
            # wrapped
            # here into ResolverError (an CodeOracleError-derived service
            # exception)
            # before reaching the Verifier (Plan 9). Closes D-8.6.
            raise ResolverError(
                "GraphQueryResolver could not run cypher "
                f"{handle.locator!r} at "
                f"snapshot {head_sha!r}: {e}"
            ) from e
        return stringify_cypher_rows(rows)
