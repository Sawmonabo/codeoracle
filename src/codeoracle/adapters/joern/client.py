"""JoernClient — per-file CPG extraction.

Plan 5 scope is intentionally narrow: extract ONE Python source file
to a CPG, export to neo4j-csv format, return the export's directory
path. The parser (Plan 5 Task 11 Step 4) consumes the export.

Why subprocess + not the Joern HTTP server? The HTTP server boots a
JVM once per process — fine for high-concurrency. The CLI boots a JVM
per invocation — slow per call but stateless. Plan 5 prefers stateless
(workers can crash without leaving a hung server) and pays the cold-
start cost.

Reconciliation with the spec §8.5 cold-reindex budget ("Full reindex
(cold, 3K LOC) < 3min p95"). The budget holds at slice scale under
per-file JVM boot, for three reasons:
  1. Scale. The slice's cold-reindex target IS the ~3K-LOC `codeoracle/`
     codebase itself (a few dozen `.py` files), NOT large external target
     repositories. A JVM cold-start + single-file parse/export is seconds,
     not minutes; a few dozen files clears <3min with margin.
  2. Parallelism is the throughput lever, not per-call latency.
     `joern_indexer` workers are independent processes on ONE
     `STREAM_INDEXING_JOERN` consumer group (`count_per_poll=1`), so
     running N worker replicas makes wall-clock about ceil(files / N) x
     per-file-cost, NOT files x per-file-cost. The per-file JVM boot
     parallelizes across replicas with no shared state to contend on —
     the exact property the stateless-CLI choice buys.
  3. The budget is MEASURED, not gated. §8.5 records cold-reindex p95
     as a CI artifact ("not gate blockers at slice stage"), so the
     trade-off is observable per PR and a regression is visible before
     it bites.
Trade-off accepted: per-file JVM boot wastes the warm-JVM amortization
the HTTP server would give. Deferral (owner: post-slice perf hardening,
alongside §8.6 "Distributed worker scaling" → Phase 8): IF a
production-scale target repository blows the budget, the optimization
is a persistent Joern server OR batch-N-files-per-JVM-boot — both
re-introduce the hung-server failure mode this plan deliberately avoids
at slice stage, so they are deferred until a measured budget breach
justifies the added recovery complexity.

Error mapping:
  - Non-zero exit → JoernExtractionError (terminal — file syntax
    failed; retrying won't help).
  - Subprocess timeout → JoernExtractionError too (treat as terminal
    since timeouts on simple files indicate joern-cli misconfig, not
    a transient blip).
  - File missing → JoernExtractionError (caller bug).
"""

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.core.exceptions import JoernExtractionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JoernExportResult:
    """Output of one extract() call.

    Attributes: export_dir: directory containing nodes_*.csv + edges_*.csv
            (neo4j-csv format). Caller must rmtree() when done.
        file_path: the source file extracted (echoed for convenience).
    """

    export_dir: Path
    file_path: str


class JoernClient:
    def __init__(self, config: JoernConfig) -> None:
        self._config = config
        # Ensure the work_dir exists at construct time so first extract
        # doesn't fail on a missing parent.
        Path(self._config.work_dir).mkdir(parents=True, exist_ok=True)

    @property
    def worst_case_extraction_seconds(self) -> int:
        """Upper bound on one extract() call: parse + export run back-to-back,
        each capped at its own subprocess timeout. The joern_indexer consumer
        sizes its reclaim min-idle off this so XAUTOCLAIM never re-claims an
        extraction that is still legitimately running.
        """
        return (
            self._config.parse_timeout_seconds
            + self._config.export_timeout_seconds
        )

    def extract(self, file_path: Path) -> JoernExportResult:
        """Parse `file_path` to a CPG and export to neo4j-csv.

        Caller (joern_indexer worker) is responsible for rmtree() on
        the export_dir when done — JoernClient does not auto-clean.
        """
        if not file_path.is_file():
            raise JoernExtractionError(
                f"Source file does not exist: {file_path}"
            )

        scratch = Path(
            tempfile.mkdtemp(prefix="joern-", dir=self._config.work_dir)
        )
        cpg_path = scratch / "cpg.bin"
        export_dir = scratch / "export"

        # 1) Parse
        parse_cmd = [
            self._config.joern_parse_bin,
            "--language",
            # matches Plan 2's verification-gate invocation byte-for-byte
            "pythonsrc",
            "--output",
            str(cpg_path),
            str(file_path),
        ]
        self._run(
            parse_cmd,
            timeout=self._config.parse_timeout_seconds,
            phase="parse",
        )

        # 2) Export to neo4j-csv
        export_dir.mkdir(parents=True, exist_ok=True)
        export_cmd = [
            self._config.joern_export_bin,
            "--repr",
            "cpg14",
            "--format",
            "neo4jcsv",
            "--out",
            str(export_dir),
            str(cpg_path),
        ]
        self._run(
            export_cmd,
            timeout=self._config.export_timeout_seconds,
            phase="export",
        )

        return JoernExportResult(
            export_dir=export_dir, file_path=str(file_path)
        )

    def cleanup(self, result: JoernExportResult) -> None:
        """Remove the scratch dir associated with `result.export_dir`."""
        scratch_root = result.export_dir.parent
        try:
            shutil.rmtree(scratch_root, ignore_errors=True)
        except OSError as exc:
            logger.warning(
                "joern_cleanup_failed",
                extra={"scratch": str(scratch_root), "error": str(exc)},
            )

    def _run(self, cmd: list[str], *, timeout: int, phase: str) -> None:
        """
        subprocess.run wrapper translating failures to JoernExtractionError.
        """
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise JoernExtractionError(
                f"joern-{phase} timed out after {timeout}s. cmd={cmd[:2]}..."
            ) from exc
        except FileNotFoundError as exc:
            # joern binary missing on PATH — config error, but raise as
            # extraction error so workers dead-letter the message rather
            # than crash-loop forever.
            raise JoernExtractionError(
                "joern binary not found: "
                f"{cmd[0]!r} — verify CODEORACLE_JOERN__*"
            ) from exc

        if result.returncode != 0:
            raise JoernExtractionError(
                f"joern-{phase} exited {result.returncode}. "
                f"stderr={result.stderr[:500]!r}"
            )
