"""ScipClient — subprocess wrapper for scip-python + scip.

These tests use the `unittest.mock` patch on `asyncio.create_subprocess_exec`
to validate the argv structure WITHOUT actually invoking scip-python (which
isn't available in CI in all environments — although Plan 2's preflight does
verify it locally).

Live e2e exercise of the real binary happens in the SCIP worker integration
test (Task 10) inside the testcontainers slice.
"""

import asyncio
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.core.exceptions import ScipExtractionError


@pytest.mark.asyncio
async def test_index_invokes_scip_python_with_correct_argv(
    tmp_path: pathlib.Path,
) -> None:
    """The SCIP client shells out with the expected argv shape."""
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "index.scip"

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
    ) as p:
        client = ScipClient()
        await client.index_project(
            project_dir=str(project),
            project_name="codeoracle",
            output_path=str(output),
        )
        # Verify argv:
        called_args = p.call_args[0]
        assert called_args[0] == "scip-python"
        assert called_args[1] == "index"
        assert "--project-name" in called_args
        assert "codeoracle" in called_args
        assert "--output" in called_args
        assert str(output) in called_args
        assert str(project) in called_args


@pytest.mark.asyncio
async def test_index_raises_scip_extraction_error_on_nonzero_exit(
    tmp_path: pathlib.Path,
) -> None:
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"oh no"))
    mock_proc.returncode = 2

    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
    ):
        client = ScipClient()
        with pytest.raises(ScipExtractionError) as exc_info:
            await client.index_project(
                project_dir=str(tmp_path),
                project_name="codeoracle",
                output_path=str(tmp_path / "i.scip"),
            )
        # Error message includes truncated stderr for triage.
        assert "oh no" in str(exc_info.value)


@pytest.mark.asyncio
async def test_print_index_invokes_scip_with_correct_argv(
    tmp_path: pathlib.Path,
) -> None:
    scip_file = tmp_path / "index.scip"
    scip_file.write_bytes(b"fake")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"text-output", b""))
    mock_proc.returncode = 0

    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
    ) as p:
        client = ScipClient()
        text = await client.print_index(scip_file=str(scip_file))
        assert text == "text-output"
        called_args = p.call_args[0]
        assert called_args[0] == "scip"
        assert called_args[1] == "print"
        assert str(scip_file) in called_args


@pytest.mark.asyncio
async def test_print_index_raises_on_nonzero_exit(
    tmp_path: pathlib.Path,
) -> None:
    scip_file = tmp_path / "index.scip"
    scip_file.write_bytes(b"fake")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"corrupted file"))
    mock_proc.returncode = 1
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
    ):
        client = ScipClient()
        with pytest.raises(ScipExtractionError) as exc_info:
            await client.print_index(scip_file=str(scip_file))
        assert "corrupted file" in str(exc_info.value)


@pytest.mark.asyncio
async def test_index_kills_and_raises_on_timeout(
    tmp_path: pathlib.Path,
) -> None:
    """A hung scip-python is killed and surfaced as ScipExtractionError, not
    left to wedge the handle and stall the reindex barrier. Bounding the handle
    this way is the precondition for ever turning stream reclaim on."""

    async def _hang() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return (b"", b"")

    mock_proc = AsyncMock()
    mock_proc.communicate = _hang
    mock_proc.kill = MagicMock()  # Process.kill() is synchronous
    mock_proc.wait = AsyncMock()

    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
    ):
        client = ScipClient(index_timeout_seconds=0.01)
        with pytest.raises(ScipExtractionError, match="index timed out"):
            await client.index_project(
                project_dir=str(tmp_path),
                project_name="codeoracle",
                output_path=str(tmp_path / "i.scip"),
            )
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_print_kills_and_raises_on_timeout(
    tmp_path: pathlib.Path,
) -> None:
    """
    Same liveness bound on the print phase, sized off print_timeout_seconds.
    """
    scip_file = tmp_path / "index.scip"
    scip_file.write_bytes(b"fake")

    async def _hang() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return (b"", b"")

    mock_proc = AsyncMock()
    mock_proc.communicate = _hang
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
    ):
        client = ScipClient(print_timeout_seconds=0.01)
        with pytest.raises(ScipExtractionError, match="print timed out"):
            await client.print_index(scip_file=str(scip_file))
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()
