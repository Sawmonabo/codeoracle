#!/usr/bin/env python3
"""Assert CodeOracle has ingested its own traces into Postgres."""

import argparse
import asyncio
import os
import sys

import asyncpg


def _out(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(message + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Assert the traces ingest path populated traces.spans and "
            "traces.ingest_cursor in the compose Postgres database."
        )
    )
    parser.add_argument(
        "--host",
        default=os.getenv("CODEORACLE_DB__HOST", "localhost"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CODEORACLE_DB__PORT", "5432")),
    )
    parser.add_argument(
        "--user",
        default=os.getenv("CODEORACLE_DB__USER", "codeoracle"),
    )
    parser.add_argument(
        "--password",
        default=os.getenv("CODEORACLE_DB__PASSWORD", "codeoracle"),
    )
    parser.add_argument(
        "--database",
        default=os.getenv("CODEORACLE_DB__DATABASE", "codeoracle"),
    )
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    conn = await asyncpg.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
    )
    try:
        span_count = await conn.fetchval("SELECT COUNT(*) FROM traces.spans")
        cursor_count = await conn.fetchval(
            "SELECT COUNT(*) FROM traces.ingest_cursor"
        )
    finally:
        await conn.close()

    if span_count <= 0:
        _out("FAIL: traces.spans has no ingested spans", error=True)
        return 1
    if cursor_count <= 0:
        _out(
            "FAIL: traces.ingest_cursor has no persisted cursor",
            error=True,
        )
        return 1

    _out(
        "OK: traces ingestion populated "
        f"{span_count} spans and {cursor_count} cursor row(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
