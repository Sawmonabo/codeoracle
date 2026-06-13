"""Integration test: bootstrap_dev.sh produces a working environment."""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = REPO_ROOT / "infra" / "scripts" / "bootstrap_dev.sh"


@pytest.mark.integration
class TestBootstrapDev:
    def test_script_exists_and_is_executable(self) -> None:
        assert BOOTSTRAP.exists(), f"{BOOTSTRAP} not found"
        assert os.access(BOOTSTRAP, os.X_OK), f"{BOOTSTRAP} is not executable"

    def test_idempotent_on_already_bootstrapped_repo(self) -> None:
        # The current repo IS bootstrapped (CI runs after the bootstrap step).
        # Running again must succeed and not destroy state.
        result = subprocess.run(
            ["bash", str(BOOTSTRAP)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"stderr:\n{result.stderr}"

    def test_produces_runnable_python(self) -> None:
        # After bootstrap, `uv run python --version` reports 3.14.x
        result = subprocess.run(
            ["uv", "run", "python", "--version"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        # uv routes "python --version" to stdout
        version_str = (result.stdout + result.stderr).strip()
        assert "Python 3.14" in version_str
