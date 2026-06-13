"""Integration tests: Dockerfile builds and resulting containers run.

These tests require Docker daemon access. CI runs them on a runner with
the docker-in-docker buildkit driver.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _docker_build(variant: str, tag: str) -> None:
    result = subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            f"VARIANT={variant}",
            "-f",
            str(REPO_ROOT / "infra" / "docker" / "Dockerfile"),
            "-t",
            tag,
            str(REPO_ROOT),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(
            "docker build failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def _docker_run(
    tag: str,
    cmd: list[str],
    *,
    entrypoint: str | None = None,
) -> subprocess.CompletedProcess[str]:
    # The unified Dockerfile sets ENTRYPOINT to entrypoint.sh, which execs
    # `python -m codeoracle.$CODEORACLE_VARIANT "$@"`. Any cmd we pass without
    # an
    # entrypoint override becomes argv to codeoracle.cli, not a standalone
    # command. Pass `entrypoint=` for diagnostic checks (python, id) that
    # must run directly.
    args = ["docker", "run", "--rm"]
    if entrypoint is not None:
        args += ["--entrypoint", entrypoint]
    args += [tag, *cmd]
    return subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )


@pytest.mark.integration
class TestUnifiedDockerfile:
    def test_build_with_cli_variant(self) -> None:
        _docker_build(variant="cli", tag="codeoracle:test-cli")

    def test_container_imports_oracle(self) -> None:
        # _docker_build was a side effect of the prior test; re-run to be safe
        _docker_build(variant="cli", tag="codeoracle:test-cli")
        result = _docker_run(
            "codeoracle:test-cli",
            ["-c", "import codeoracle; print('ok')"],
            entrypoint="python",
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_container_runs_as_non_root(self) -> None:
        _docker_build(variant="cli", tag="codeoracle:test-cli")
        result = _docker_run("codeoracle:test-cli", ["-u"], entrypoint="id")
        assert result.returncode == 0
        assert result.stdout.strip() == "1000"
