#!/usr/bin/env python3
"""Preflight probe for the SCIP Python indexer contract."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _out(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(message + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default="infra/preflight-fixtures/sample_py.py",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    fixture = Path(args.fixture)
    if not fixture.exists():
        _out(f"FAIL: fixture missing: {fixture}", error=True)
        return 2

    npx = shutil.which("npx")
    if npx is None:
        _out("FAIL: npx is not installed", error=True)
        return 127

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / fixture.name
        target.write_text(fixture.read_text())
        index_path = tmp_path / "index.scip"
        subprocess.run(
            [
                npx,
                "--yes",
                "@sourcegraph/scip-python",
                "index",
                "--project-name=codeoracle",
                f"--output={index_path.name}",
                ".",
            ],
            cwd=tmp_path,
            check=True,
        )
        if not index_path.exists() or index_path.stat().st_size == 0:
            _out("FAIL: SCIP index was not created", error=True)
            return 1
        printed = subprocess.run(
            [npx, "--yes", "@sourcegraph/scip", "print", str(index_path)],
            cwd=tmp_path,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        if "verify" not in printed.stdout:
            _out(
                "FAIL: SCIP output did not contain fixture symbol",
                error=True,
            )
            return 1
    _out("OK: scip-python emitted a resolvable index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
