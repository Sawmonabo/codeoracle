"""Tests for .github/workflows/image-build.yml."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGE_BUILD_WF = REPO_ROOT / ".github" / "workflows" / "image-build.yml"


def test_workflow_exists() -> None:
    assert IMAGE_BUILD_WF.exists()


def test_workflow_is_valid_yaml() -> None:
    with IMAGE_BUILD_WF.open() as f:
        yaml.safe_load(f)


def test_workflow_uses_matrix_over_variants() -> None:
    with IMAGE_BUILD_WF.open() as f:
        data = yaml.safe_load(f)
    jobs = data["jobs"]
    # Find the build job (likely named build or image-build)
    build_job = None
    for job in jobs.values():
        if "strategy" in job and "matrix" in job["strategy"]:
            build_job = job
            break
    assert build_job is not None, "No matrix-strategy job found"

    matrix = build_job["strategy"]["matrix"]
    variants = matrix.get("variant") or matrix.get("VARIANT") or []
    expected = {"api", "worker", "cli", "mcp", "agent"}
    assert expected.issubset(set(variants)), (
        f"Missing variants: {expected - set(variants)}"
    )


def test_workflow_runs_trivy() -> None:
    text = IMAGE_BUILD_WF.read_text()
    assert "trivy" in text.lower()


def test_workflow_fails_on_high_severity() -> None:
    text = IMAGE_BUILD_WF.read_text().lower()
    assert "high" in text or "critical" in text
    # Reasonable check: severity threshold appears in the same workflow
    assert "severity" in text or "exit-code" in text
