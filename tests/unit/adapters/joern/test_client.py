"""Plan 5 Task 11: JoernClient unit tests with mocked subprocess."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.core.exceptions import JoernExtractionError


@pytest.fixture
def joern_config(tmp_path):
    return JoernConfig(
        joern_parse_bin="joern-parse",
        joern_export_bin="joern-export",
        parse_timeout_seconds=60,
        export_timeout_seconds=60,
        work_dir=str(tmp_path / "joern-work"),
    )


def _ok_subprocess_result(returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=""
    )


def test_extract_creates_export_dir(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with patch("subprocess.run", return_value=_ok_subprocess_result()):
        result = client.extract(src)
    assert result.file_path == str(src)
    assert result.export_dir.exists()
    assert result.export_dir.name == "export"


def test_extract_missing_file_raises(joern_config):
    client = JoernClient(joern_config)
    with pytest.raises(JoernExtractionError, match="does not exist"):
        client.extract(Path("/no/such/file.py"))


def test_extract_nonzero_exit_raises(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    bad = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="syntax error at line 3",
    )
    with (
        patch("subprocess.run", return_value=bad),
        pytest.raises(JoernExtractionError, match=r"exited 1"),
    ):
        client.extract(src)


def test_extract_timeout_raises(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["joern-parse"], timeout=60
            ),
        ),
        pytest.raises(JoernExtractionError, match="timed out"),
    ):
        client.extract(src)


def test_extract_missing_binary_raises(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with (
        patch("subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(JoernExtractionError, match="not found"),
    ):
        client.extract(src)


def test_cleanup_removes_scratch(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with patch("subprocess.run", return_value=_ok_subprocess_result()):
        result = client.extract(src)
    scratch_root = result.export_dir.parent
    assert scratch_root.exists()
    client.cleanup(result)
    assert not scratch_root.exists()
