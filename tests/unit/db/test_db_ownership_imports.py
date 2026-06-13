import importlib.util
import re
from pathlib import Path

import codeoracle.db.models
from codeoracle.db.orm import Base
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
    SnapshotFileOutcomeRow,
)


def test_orm_tables_are_owned_by_db_models() -> None:
    assert codeoracle.db.models is not None
    assert "code_references" in Base.metadata.tables
    assert "embeddings" in Base.metadata.tables
    assert "snapshots" in Base.metadata.tables
    assert "snapshot_file_outcomes" in Base.metadata.tables


def test_db_models_expose_explicit_record_names() -> None:
    assert codeoracle.db.models.CodeReferenceRecord.__name__ == (
        "CodeReferenceRecord"
    )
    assert codeoracle.db.models.EmbeddingRecord.__name__ == "EmbeddingRecord"
    assert codeoracle.db.models.SnapshotRecord.__name__ == "SnapshotRecord"
    assert codeoracle.db.models.SnapshotFileOutcomeRecord.__name__ == (
        "SnapshotFileOutcomeRecord"
    )


def test_removed_schema_and_file_outcomes_modules_are_not_importable() -> None:
    assert importlib.util.find_spec("codeoracle.db." + "schemas") is None
    assert (
        importlib.util.find_spec(
            "codeoracle.db.repositories." + "file_outcomes"
        )
        is None
    )


def test_snapshot_file_outcome_module_owns_repository_classes() -> None:
    assert (
        SnapshotFileOutcomeRepository.__module__
        == "codeoracle.db.repositories.snapshot_file_outcome"
    )
    assert (
        SnapshotFileOutcomeRow.__module__
        == "codeoracle.db.repositories.snapshot_file_outcome"
    )


def test_no_row_class_import_aliases_remain() -> None:
    source_root = Path(__file__).parents[2] / "src" / "codeoracle"
    alias_re = re.compile(
        r"from\s+codeoracle\.db\.[^\n]+import\s+[^\n]+"
        r"\s+as\s+(?:[A-Za-z]+Row|[A-Za-z]+Record|SfoRow)\b"
    )
    offenders = []
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if alias_re.search(text):
            offenders.append(str(path.relative_to(source_root)))

    assert offenders == []
