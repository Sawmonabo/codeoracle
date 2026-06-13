"""Tests for the top-level Makefile."""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"


def test_makefile_exists() -> None:
    assert MAKEFILE.exists()


def test_make_help_lists_targets() -> None:
    result = subprocess.run(
        ["make", "help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    expected_targets = [
        "bootstrap",
        "test",
        "lint",
        "type-check",
        "compose-up",
        "compose-down",
    ]
    for t in expected_targets:
        assert t in result.stdout, f"`make help` does not list {t!r}"


def test_make_lint_runs_ruff() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "lint"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert "ruff" in result.stdout


def test_make_test_runs_pytest() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "test"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert "pytest" in result.stdout
