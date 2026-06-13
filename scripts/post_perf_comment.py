#!/usr/bin/env python3
"""Post a slice performance summary to a GitHub pull request."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _out(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(message + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Format perf-report.json and post it with gh pr comment."
    )
    parser.add_argument("--report", default="perf-report.json")
    parser.add_argument("--pr", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, (dict, list)):
        return "`" + json.dumps(value, sort_keys=True) + "`"
    return str(value)


def _markdown(report: dict[str, Any]) -> str:
    rows = [
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for key in sorted(report):
        rows.append(f"| `{key}` | {_format_value(report[key])} |")
    return "\n".join(["## CodeOracle Slice Performance", "", *rows, ""])


def main() -> int:
    args = _parser().parse_args()
    report_path = Path(args.report)
    report = json.loads(report_path.read_text())
    if not isinstance(report, dict):
        raise TypeError(f"{report_path} must contain a JSON object")
    body = _markdown(report)

    if args.dry_run:
        _out(body)
        return 0

    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        fh.write(body)
        temp_path = fh.name
    try:
        subprocess.run(
            ["gh", "pr", "comment", str(args.pr), "--body-file", temp_path],
            check=True,
        )
    except FileNotFoundError:
        _out("FAIL: gh CLI is not installed", error=True)
        return 127
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
