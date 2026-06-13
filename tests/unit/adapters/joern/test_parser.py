"""Plan 5 Task 11: JoernCpgParser unit tests using the checked-in fixture."""

from pathlib import Path
from tempfile import TemporaryDirectory

from codeoracle.adapters.joern.parser import JoernCpgParser, _derive_dotted_fqn

FIXTURE = (
    Path(__file__).parent.parent.parent.parent
    / "fixtures/joern/single_file_cpg"
)


def test_parser_emits_file_node():
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=FIXTURE,
        snapshot_sha="abc1234",
        file_path="src/example.py",
    )
    file_stmts = [s for s in stmts if s.startswith("CREATE (:File ")]
    assert len(file_stmts) == 1
    assert "snapshot_sha:'abc1234'" in file_stmts[0]
    assert "file_path:'src/example.py'" in file_stmts[0]


def test_parser_emits_symbol_nodes_with_fqn():
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=FIXTURE,
        snapshot_sha="abc1234",
        file_path="src/example.py",
    )
    symbol_stmts = [s for s in stmts if s.startswith("CREATE (:Symbol ")]
    assert len(symbol_stmts) == 2
    # Dotted Python FQN — see "Cross-plan contracts" at the top of the plan.
    # Plan 8 (graph-evidence resolver) + Plan 10 (is_safe_symbol_fqn predicate)
    # both query the graph using this form.
    assert any("fqn:'example.foo'" in s for s in symbol_stmts)
    assert any("fqn:'example.bar'" in s for s in symbol_stmts)
    # Raw facts preserved so a future Plan 6 derivation pass can refine
    # the FQN without re-extracting from source.
    assert all("name:" in s and "file_path:" in s for s in symbol_stmts)


def test_derive_dotted_fqn_examples():
    """
    Pin the FQN derivation algorithm — changing it forces a graph re-index.
    """
    # Module file + free symbol.
    assert (
        _derive_dotted_fqn("src/codeoracle/utils.py", "foo")
        == "codeoracle.utils.foo"
    )
    # Package __init__.py collapses to the package.
    assert (
        _derive_dotted_fqn("src/codeoracle/__init__.py", "foo")
        == "codeoracle.foo"
    )
    # Nested module path.
    assert (
        _derive_dotted_fqn(
            "src/codeoracle/adapters/joern/client.py", "JoernClient"
        )
        == "codeoracle.adapters.joern.client.JoernClient"
    )
    # Empty name returns the module FQN.
    assert (
        _derive_dotted_fqn("src/codeoracle/utils.py", "") == "codeoracle.utils"
    )
    # Path without src/ prefix is passed through (file not under project root).
    assert _derive_dotted_fqn("vendor/lib.py", "foo") == "vendor.lib.foo"


def test_parser_emits_calls_edge():
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=FIXTURE,
        snapshot_sha="abc1234",
        file_path="src/example.py",
    )
    calls_stmts = [s for s in stmts if ":CALLS" in s]
    assert len(calls_stmts) == 1
    # MATCH by joern_id from edges_CALLS.csv
    assert "joern_id:'3'" in calls_stmts[0]
    assert "joern_id:'2'" in calls_stmts[0]


def test_parser_skips_unknown_labels(tmp_path):
    """Unknown nodes_*.csv files are silently ignored."""
    # Create a fake export dir with one unknown label
    (tmp_path / "nodes_LITERAL.csv").write_text(":ID,name\n1,x\n")
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=tmp_path,
        snapshot_sha="abc",
        file_path="x.py",
    )
    assert stmts == []  # nothing emitted


def test_parser_handles_empty_export(tmp_path):
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=tmp_path,
        snapshot_sha="abc",
        file_path="x.py",
    )
    assert stmts == []


def test_escape_quotes_in_node_property():
    """A name containing ' must be safely escaped."""
    parser = JoernCpgParser()
    # Build a minimal one-node fixture with a quote in the name
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "nodes_METHOD.csv").write_text(
            ":ID,name,LINE_NUMBER\n1,quote'd,5\n"
        )
        stmts = parser.to_cypher(
            export_dir=tmp,
            snapshot_sha="abc",
            file_path="x.py",
        )
        assert any(r"quote\'d" in s for s in stmts), (
            f"Expected escaped single quote in: {stmts}"
        )
