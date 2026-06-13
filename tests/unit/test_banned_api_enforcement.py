"""Plan 4 Task 13: Prove the structural-enforcement contract works.

If this test fails, the ruff configuration in pyproject.toml has drifted
and the platform's "no direct LLM SDK imports outside providers/"
guarantee is broken — meaning Plan 4's Plan 5+ inheritance contract no
longer holds.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff(target_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(REPO_ROOT / "pyproject.toml"),
            "--select",
            "TID251",
            str(target_file),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


@pytest.mark.parametrize(
    "sdk_name",
    [
        "openai",
        "anthropic",
        "litellm",
        "ollama",
        "redis",
        "redis.asyncio",
        "valkey.asyncio",
    ],
)
def test_banned_api_blocks_direct_import_outside_providers(tmp_path, sdk_name):
    """A non-adapter file importing a banned SDK must fail."""
    bad_file = tmp_path / "fake_service.py"
    bad_file.write_text(f"import {sdk_name}\n")

    result = _run_ruff(bad_file)

    assert result.returncode != 0, (
        f"ruff did NOT flag 'import {sdk_name}' in a non-providers file. "
        "banned-api enforcement is broken.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert sdk_name in result.stdout or sdk_name in result.stderr


def test_banned_api_allows_import_in_providers_file():
    """
    The per-file-ignore for src/codeoracle/adapters/llm/providers/*.py must
    work. We point ruff at the real openai.py (which contains `import openai`)
    and expect zero TID251 violations."""
    target = (
        REPO_ROOT
        / "src"
        / "codeoracle"
        / "adapters"
        / "llm"
        / "providers"
        / "openai.py"
    )
    assert target.exists(), f"Plan 4 Task 8 not yet applied: {target} missing"

    result = _run_ruff(target)
    assert result.returncode == 0, (
        f"ruff flagged the openai SDK import inside providers/openai.py — "
        "per-file-ignore for "
        "src/codeoracle/adapters/llm/providers/*.py is broken.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_banned_api_allows_import_in_valkey_adapter_file():
    """The Valkey adapter may import valkey.asyncio."""
    target = (
        REPO_ROOT / "src" / "codeoracle" / "adapters" / "valkey" / "client.py"
    )
    assert target.exists(), f"Valkey adapter missing: {target}"

    result = _run_ruff(target)
    assert result.returncode == 0, (
        "ruff flagged valkey.asyncio inside adapters/valkey/client.py — "
        "per-file-ignore for the Valkey adapter is broken.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_banned_api_message_includes_actionable_redirect(tmp_path):
    """The ruff message should tell the developer where to import instead.
    This is a documentation property — broken message = developers
    re-discover the convention from scratch."""
    bad_file = tmp_path / "fake_service.py"
    bad_file.write_text("import openai\n")

    result = _run_ruff(bad_file)
    assert "codeoracle.adapters.llm" in (result.stdout + result.stderr), (
        "banned-api message doesn't mention codeoracle.adapters.llm — "
        "developers won't know where to import from"
    )
