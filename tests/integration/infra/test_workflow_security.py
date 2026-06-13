"""Tests for .github/workflows/security.yml."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SECURITY_WF = REPO_ROOT / ".github" / "workflows" / "security.yml"
CI_WF = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_security_workflow_exists() -> None:
    assert SECURITY_WF.exists()


def test_security_workflow_is_valid_yaml() -> None:
    with SECURITY_WF.open() as f:
        data = yaml.safe_load(f)
    # GitHub Actions parses `on:` as True due to YAML 1.1 truthiness — check
    # both keys.
    assert "jobs" in data
    assert "on" in data or True in data


def test_security_workflow_runs_gitleaks() -> None:
    text = SECURITY_WF.read_text()
    assert "gitleaks" in text.lower()


def test_security_workflow_runs_semgrep() -> None:
    text = SECURITY_WF.read_text()
    assert "semgrep" in text.lower()


def test_security_workflow_uploads_sarif() -> None:
    text = SECURITY_WF.read_text()
    assert "sarif" in text.lower()


def test_ci_workflow_no_longer_runs_gitleaks() -> None:
    # gitleaks moved out of ci.yml — security.yml owns it now.
    text = CI_WF.read_text()
    assert "gitleaks" not in text.lower(), (
        "ci.yml still references gitleaks — should have been removed when "
        "security.yml took ownership"
    )


def test_ci_workflow_no_longer_runs_semgrep() -> None:
    text = CI_WF.read_text()
    assert "semgrep" not in text.lower(), (
        "ci.yml still references semgrep — should have been removed when "
        "security.yml took ownership"
    )
