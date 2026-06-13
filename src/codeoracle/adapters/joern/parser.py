"""JoernCpgParser — convert joern's neo4j-csv export to AGE Cypher
INSERT statements.

joern-export --format=neo4jcsv produces per-label files:
    nodes_FILE.csv, nodes_METHOD.csv, nodes_CALL.csv, ...
    edges_CALLS.csv, edges_USES.csv, ...

Plan 5 scope: parser handles FILE, METHOD, CALL nodes and CALLS,
DEFINES, IMPORTS edges. Other labels are silently skipped — the
parser doesn't reject unknown labels because joern emits many label
types we don't yet use; lifting the filter into the schema is a Plan
6/7/8 concern as the agent's queries demand more.

Output is a list of Cypher CREATE statements, each one stamped with
(snapshot_sha, file_path) so the per-file delete-then-insert in
GraphRepository.replace_file_subgraph() catches the right subgraph.
"""

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


# Map joern's neo4j-csv label files → AGE label we want.
_NODE_LABEL_MAP: Final[dict[str, str]] = {
    "FILE": "File",
    "METHOD": "Symbol",  # method-shaped definitions become :Symbol
    "CALL": "Call",  # call-site placeholders
    # Joern emits dozens more; Plan 5 only cares about these. Extend in Plan 6
    # (additional Joern node kinds for SCIP/embed integration).
}

_EDGE_LABEL_MAP: Final[dict[str, str]] = {
    "CALLS": "CALLS",
    "DEFINES": "DEFINES",
    "IMPORTS": "IMPORTS",
}


@dataclass(frozen=True)
class JoernCpgParser:
    """Stateless converter — construct once, parse many."""

    def to_cypher(
        self,
        *,
        export_dir: Path,
        snapshot_sha: str,
        file_path: str,
    ) -> list[str]:
        """Return Cypher CREATE statements representing this file's
        subgraph at `snapshot_sha`. Caller passes the list to
        GraphRepository.replace_file_subgraph().
        """
        statements: list[str] = []
        # Unmapped joern labels are non-fatal (Plan 5 only models File / Symbol
        # /
        # Call / CALLS / DEFINES / IMPORTS) but must be observable —
        # silent skip would hide schema drift if joern's export shape changes
        # in a future release. Aggregated and logged once per parse at DEBUG.
        skipped_labels: dict[str, int] = {}

        # 1) Nodes
        for csv_path in export_dir.glob("nodes_*.csv"):
            joern_label = csv_path.stem.removeprefix("nodes_")
            age_label = _NODE_LABEL_MAP.get(joern_label)
            if age_label is None:
                key = f"node:{joern_label}"
                skipped_labels[key] = skipped_labels.get(key, 0) + 1
                continue
            statements.extend(
                self._node_statements(
                    csv_path=csv_path,
                    age_label=age_label,
                    snapshot_sha=snapshot_sha,
                    file_path=file_path,
                )
            )

        # 2) Edges (built second so node IDs exist when MATCH'd)
        for csv_path in export_dir.glob("edges_*.csv"):
            joern_label = csv_path.stem.removeprefix("edges_")
            age_label = _EDGE_LABEL_MAP.get(joern_label)
            if age_label is None:
                key = f"edge:{joern_label}"
                skipped_labels[key] = skipped_labels.get(key, 0) + 1
                continue
            statements.extend(
                self._edge_statements(
                    csv_path=csv_path,
                    age_label=age_label,
                    snapshot_sha=snapshot_sha,
                )
            )

        if skipped_labels:
            logger.debug(
                "joern_parser: skipped %d unmapped CSV file(s) for %s — %s",
                sum(skipped_labels.values()),
                file_path,
                skipped_labels,
            )

        return statements

    @staticmethod
    def _node_statements(
        *,
        csv_path: Path,
        age_label: str,
        snapshot_sha: str,
        file_path: str,
    ) -> list[str]:
        out: list[str] = []
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                joern_id = row.get(":ID") or row.get("id:ID")
                if not joern_id:
                    continue
                name = (
                    row.get("name")
                    or row.get("FULL_NAME")
                    or row.get("CODE")
                    or ""
                )
                line = row.get("LINE_NUMBER") or row.get("lineNumber") or ""
                # Build the CREATE — properties JSON-escaped via json.dumps
                # then stripped of the outer braces for inlining
                props_obj = {
                    "joern_id": joern_id,
                    "snapshot_sha": snapshot_sha,
                    "file_path": file_path,
                    "name": name,
                    "line": line,
                }
                if age_label == "Symbol":
                    # Dotted Python FQN — contract shared with Plan 8's
                    # graph-evidence resolver + Plan 10's is_safe_symbol_fqn
                    # predicate. See top-of-plan "Cross-plan contracts".
                    props_obj["fqn"] = _derive_dotted_fqn(file_path, name)
                props_cypher = _props_to_cypher_object(props_obj)
                out.append(f"CREATE (:{age_label} {props_cypher})")
        return out

    @staticmethod
    def _edge_statements(
        *,
        csv_path: Path,
        age_label: str,
        snapshot_sha: str,
    ) -> list[str]:
        out: list[str] = []
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                start_id = row.get(":START_ID") or row.get("start:START_ID")
                end_id = row.get(":END_ID") or row.get("end:END_ID")
                if not start_id or not end_id:
                    continue
                # MATCH by joern_id (the export-time IDs)
                out.append(
                    f"MATCH (a {{joern_id:'{_escape(start_id)}', "
                    f"snapshot_sha:'{_escape(snapshot_sha)}'}}), "
                    f"(b {{joern_id:'{_escape(end_id)}', "
                    f"snapshot_sha:'{_escape(snapshot_sha)}'}}) "
                    f"CREATE (a)-[:{age_label}]->(b)"
                )
        return out


def _derive_dotted_fqn(file_path: str, name: str) -> str:
    """Dotted Python FQN derived from (file_path, name).

    Algorithm (slice scope: src/codeoracle/* — see "Cross-plan contracts" at
    the top of the plan):
      1. Strip leading 'src/' (project package root convention).
      2. Strip '.py' suffix.
      3. Strip trailing '/__init__' (package modules collapse to the package).
      4. Replace '/' with '.'.
      5. If name is empty, return module; otherwise return f'{module}.{name}'.

    Examples: ('src/codeoracle/utils.py', 'foo')                  ->
      'codeoracle.utils.foo'
      ('src/codeoracle/__init__.py', 'foo')               -> 'codeoracle.foo'
      ('src/codeoracle/adapters/joern/client.py', 'JoernClient') ->
          'codeoracle.adapters.joern.client.JoernClient'

    Known limitation (deferred to Plan 6): class scope. Methods inside
    classes collide with free functions of the same name in the same
    module — Plan 10's verifier surfaces this as a structured "ambiguous
    match", never a silent wrong answer. A future plan (currently scoped
    to Plan 6) will extend this helper with class-scope; the specific
    Joern column / technique is that plan's design call.

    Joern's FULL_NAME column is intentionally NOT consulted — its Python
    shape is uncaptured anywhere (Plan 2 verification gap). Deriving from
    file_path + name is hermetic and testable without that precondition;
    re-derivation in Plan 6 is a pass over existing graph rows.
    """
    module = file_path
    if module.startswith("src/"):
        module = module[len("src/") :]
    if module.endswith(".py"):
        module = module[: -len(".py")]
    if module.endswith("/__init__"):
        module = module[: -len("/__init__")]
    module = module.replace("/", ".")
    if not name:
        return module
    return f"{module}.{name}"


def _props_to_cypher_object(props: dict[str, object]) -> str:
    """{key:value, ...} Cypher object literal — JSON for values."""
    parts: list[str] = []
    for k, v in props.items():
        if isinstance(v, str):
            parts.append(f"{k}:'{_escape(v)}'")
        else:
            # Numbers, bools — Cypher accepts JSON-form for these
            parts.append(f"{k}:{json.dumps(v)}")
    return "{" + ", ".join(parts) + "}"


def _escape(s: str) -> str:
    """Escape single quotes for inclusion in Cypher string literals.
    Plan 5's source is internally produced (joern-export CSV), not user
    input, so this is defensive — the threat model is "joern emits a
    method name containing a backtick", not injection.
    """
    return s.replace("\\", "\\\\").replace("'", "\\'")
