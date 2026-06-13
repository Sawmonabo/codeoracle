"""Integration test: seed_test_corpus.sh populates tests/fixtures/corpus/."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED = REPO_ROOT / "infra" / "scripts" / "seed_test_corpus.sh"
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "corpus"


@pytest.mark.integration
class TestSeedTestCorpus:
    def setup_method(self) -> None:
        if CORPUS_DIR.exists():
            shutil.rmtree(CORPUS_DIR)

    def test_script_exists_and_is_executable(self) -> None:
        assert SEED.exists()
        assert os.access(SEED, os.X_OK)

    def test_creates_corpus_directory(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        assert CORPUS_DIR.exists()
        assert CORPUS_DIR.is_dir()

    def test_copies_oracle_source(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        # Should include core/, utils/, etc.
        assert (CORPUS_DIR / "codeoracle" / "core").is_dir()
        assert (
            CORPUS_DIR / "codeoracle" / "utils" / "async_helpers.py"
        ).is_file()

    def test_excludes_pycache(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        for pycache in CORPUS_DIR.rglob("__pycache__"):
            pytest.fail(f"corpus contains __pycache__: {pycache}")

    def test_idempotent(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        first_files = sorted(
            p.relative_to(CORPUS_DIR)
            for p in CORPUS_DIR.rglob("*")
            if p.is_file()
        )
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        second_files = sorted(
            p.relative_to(CORPUS_DIR)
            for p in CORPUS_DIR.rglob("*")
            if p.is_file()
        )
        assert first_files == second_files
