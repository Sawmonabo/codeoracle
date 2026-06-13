"""
Tests for infra/security/semgrep/codeoracle-custom.yml — custom SAST rules.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_FILE = (
    REPO_ROOT / "infra" / "security" / "semgrep" / "codeoracle-custom.yml"
)


def _has_semgrep() -> bool:
    return shutil.which("semgrep") is not None


def _run_semgrep(target_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "semgrep",
            "--config",
            str(RULES_FILE),
            "--quiet",
            "--error",
            str(target_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(not _has_semgrep(), reason="semgrep not installed")
class TestSemgrepRules:
    def test_rules_file_exists(self) -> None:
        assert RULES_FILE.exists()

    def test_clean_code_passes(self, tmp_path: Path) -> None:
        # A file that imports nothing forbidden
        (tmp_path / "clean.py").write_text(
            "from codeoracle.adapters.llm import LLMProvider\n"
            "def f(p: LLMProvider) -> None:\n"
            "    pass\n"
        )
        result = _run_semgrep(tmp_path)
        assert result.returncode == 0, result.stdout

    def test_direct_anthropic_import_outside_providers_fails(
        self, tmp_path: Path
    ) -> None:
        # Simulate a service trying to import anthropic directly — banned by
        # memory project-llm-provider-agnostic.
        svc_dir = tmp_path / "services"
        svc_dir.mkdir()
        (svc_dir / "bad.py").write_text("import anthropic\n")
        result = _run_semgrep(tmp_path)
        assert result.returncode != 0
        assert "codeoracle-llm-direct-import" in result.stdout

    def test_subprocess_shell_true_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "shell_bad.py").write_text(
            "import subprocess\nsubprocess.run('ls', shell=True)\n"
        )
        result = _run_semgrep(tmp_path)
        assert result.returncode != 0
        assert "codeoracle-shell-true" in result.stdout
