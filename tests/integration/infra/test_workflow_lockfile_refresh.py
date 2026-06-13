"""Tests for .github/workflows/lockfile-refresh.yml."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WF = REPO_ROOT / ".github" / "workflows" / "lockfile-refresh.yml"


def test_workflow_exists() -> None:
    assert WF.exists()


def test_workflow_is_valid_yaml() -> None:
    with WF.open() as f:
        yaml.safe_load(f)


def test_workflow_runs_on_schedule() -> None:
    with WF.open() as f:
        data = yaml.safe_load(f)
    on_block = data.get("on") or data.get(True)
    assert "schedule" in on_block, f"on: must include schedule, got {on_block}"


def test_workflow_runs_uv_lock_upgrade() -> None:
    text = WF.read_text()
    assert "uv lock --upgrade" in text


def test_workflow_opens_pr() -> None:
    text = WF.read_text().lower()
    assert "create-pull-request" in text or "pull-request" in text
