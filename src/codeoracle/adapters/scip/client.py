"""ScipClient — argv-only subprocess wrapper.

NO `shell=True`. NO string interpolation into a shell command. All inputs
are passed as positional argv entries. This is the same safety pattern as
Plan 5's JoernClient.
"""

import asyncio
from typing import Final

from codeoracle.core.exceptions import ScipExtractionError

_STDERR_TRUNCATE: Final[int] = 4096

# Liveness ceilings for the scip subprocesses. A scip handle runs index then
# print back-to-back (count_per_poll=1), so worst-case handle = index + print
# = 360s, comfortably under the IndexingOrchestrator completion barrier (600s,
# Plan 5). Without these a hung scip-python would stall the handle forever and
# the reindex barrier could never close. They bound the handle so a hung run is
# killed and surfaced as a per-file `failed` outcome instead.
DEFAULT_SCIP_INDEX_TIMEOUT_SECONDS: Final[float] = 300.0
DEFAULT_SCIP_PRINT_TIMEOUT_SECONDS: Final[float] = 60.0


class ScipClient:
    """Wraps `scip-python index` + `scip print`.

    `index_project` runs `scip-python` to build the binary `.scip` index for
    a project directory. `print_index` runs `scip` to render that file as text.
    The combination lets us avoid linking against scip's protobuf bindings.

    Both methods raise `ScipExtractionError` on non-zero exit; the worker
    catches this and treats it as a project-level failure (architectural
    invariant 3 — all changeset files written as outcome=failed). Each
    subprocess also runs under a wall-clock timeout (`index_timeout_seconds`
    / `print_timeout_seconds`); on expiry the child is killed and the same
    `ScipExtractionError` is raised, so a hung scip run can never wedge the
    handle or stall the reindex barrier.
    """

    def __init__(
        self,
        *,
        index_timeout_seconds: float = DEFAULT_SCIP_INDEX_TIMEOUT_SECONDS,
        print_timeout_seconds: float = DEFAULT_SCIP_PRINT_TIMEOUT_SECONDS,
    ) -> None:
        self._index_timeout = index_timeout_seconds
        self._print_timeout = print_timeout_seconds

    async def _communicate_or_kill(
        self,
        proc: asyncio.subprocess.Process,
        *,
        timeout: float,
        phase: str,
    ) -> tuple[bytes, bytes]:
        """Await `proc.communicate()` under a wall-clock ceiling. On expiry,
        SIGKILL the subprocess, reap it, and raise a terminal
        `ScipExtractionError` so the worker records outcome=failed instead of
        leaving a wedged handle that would stall the reindex barrier."""
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ScipExtractionError(
                f"scip {phase} timed out after {timeout:.0f}s"
            ) from exc

    async def index_project(
        self,
        *,
        project_dir: str,
        project_name: str,
        output_path: str,
    ) -> None:
        argv = [
            "scip-python",
            "index",
            "--project-name",
            project_name,
            "--output",
            output_path,
            project_dir,
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await self._communicate_or_kill(
            proc, timeout=self._index_timeout, phase="index"
        )
        if proc.returncode != 0:
            stderr_tail = stderr.decode("utf-8", errors="replace")[
                -_STDERR_TRUNCATE:
            ]
            raise ScipExtractionError(
                f"scip-python exited {proc.returncode}: {stderr_tail}"
            )

    async def print_index(self, *, scip_file: str) -> str:
        argv = ["scip", "print", scip_file]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await self._communicate_or_kill(
            proc, timeout=self._print_timeout, phase="print"
        )
        if proc.returncode != 0:
            stderr_tail = stderr.decode("utf-8", errors="replace")[
                -_STDERR_TRUNCATE:
            ]
            raise ScipExtractionError(
                f"scip print exited {proc.returncode}: {stderr_tail}"
            )
        return stdout.decode("utf-8", errors="replace")
