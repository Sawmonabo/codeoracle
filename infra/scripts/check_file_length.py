#!/usr/bin/env python3
"""File-length gate for src/codeoracle/.

Hard ceiling: 700 lines per file — exceed and CI fails.
Soft target: 500 lines per file — exceed and CI warns.

Per docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md §3.4
and memory project-code-quality-bar. Lives at infra/scripts/ so it stays
runnable before `uv sync` has any third-party deps installed — stdlib only.
"""

import sys
from pathlib import Path

HARD_CEILING = 700
SOFT_TARGET = 500


def _write(message: str = "", *, stream=sys.stdout) -> None:
    stream.write(f"{message}\n")


def _line_count(path: Path) -> int:
    """Count newline-terminated lines, matching shell `wc -l` semantics."""
    return path.read_bytes().count(b"\n")


def main(argv: list[str]) -> int:
    src_dir = Path(argv[1]) if len(argv) > 1 else Path("src/codeoracle")
    if not src_dir.is_dir():
        _write(
            f"::error::Source directory not found: {src_dir}",
            stream=sys.stderr,
        )
        return 2

    over_ceiling: list[tuple[int, Path]] = []
    over_target: list[tuple[int, Path]] = []

    for py in sorted(src_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        n = _line_count(py)
        if n > HARD_CEILING:
            over_ceiling.append((n, py))
        elif n > SOFT_TARGET:
            over_target.append((n, py))

    if over_ceiling:
        _write(f"::error::Files exceeding {HARD_CEILING}-line hard ceiling:")
        for n, p in over_ceiling:
            _write(f"  {n} {p}")
        _write()
        _write(
            f"Above {HARD_CEILING} lines -> split into modules following "
            "the layer pattern."
        )
        return 1

    if over_target:
        _write(f"::warning::Files exceeding {SOFT_TARGET}-line soft target:")
        for n, p in over_target:
            _write(f"  {n} {p}")

    _write(f"File-length gate passed: no files exceed {HARD_CEILING} lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
