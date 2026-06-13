#!/usr/bin/env python3
"""Assert required CodeOracle span names are present in Tempo."""

import argparse
import asyncio
import os
import sys
from collections.abc import Iterable
from typing import Any

import httpx


def _out(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(message + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Assert required OTel span names exist in recent Tempo traces."
        )
    )
    parser.add_argument(
        "--tempo-url",
        default=os.getenv(
            "CODEORACLE_TRACES__TEMPO_URL", "http://localhost:3200"
        ),
    )
    parser.add_argument(
        "--required",
        required=True,
        help="Comma-separated span names that must appear.",
    )
    parser.add_argument("--limit", type=int, default=20)
    return parser


def _trace_ids(search_body: dict[str, Any]) -> list[str]:
    traces = search_body.get("traces") or []
    ids: list[str] = []
    for trace in traces:
        trace_id = trace.get("traceID") or trace.get("traceId")
        if isinstance(trace_id, str) and trace_id:
            ids.append(trace_id)
    return ids


def _span_names(trace_body: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit_spans(spans: Iterable[dict[str, Any]]) -> None:
        for span in spans:
            name = span.get("name")
            if isinstance(name, str) and name:
                names.add(name)

    data = trace_body.get("data") or {}
    resource_spans = (
        data.get("resourceSpans") or trace_body.get("resourceSpans") or []
    )
    for resource_span in resource_spans:
        scope_spans = (
            resource_span.get("scopeSpans")
            or resource_span.get("instrumentationLibrarySpans")
            or []
        )
        for scope_span in scope_spans:
            visit_spans(scope_span.get("spans") or [])

    batches = data.get("batches") or trace_body.get("batches") or []
    for batch in batches:
        scope_spans = (
            batch.get("scopeSpans")
            or batch.get("instrumentationLibrarySpans")
            or []
        )
        for scope_span in scope_spans:
            visit_spans(scope_span.get("spans") or [])

    return names


async def _main() -> int:
    args = _parser().parse_args()
    required = {
        name.strip() for name in args.required.split(",") if name.strip()
    }
    if not required:
        _out(
            "FAIL: --required did not contain any span names",
            error=True,
        )
        return 2

    base_url = args.tempo_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        search = await client.get("/api/search", params={"limit": args.limit})
        search.raise_for_status()
        ids = _trace_ids(search.json())
        if not ids:
            _out("FAIL: Tempo returned no recent traces", error=True)
            return 1

        observed: set[str] = set()
        for trace_id in ids:
            trace = await client.get(f"/api/traces/{trace_id}")
            trace.raise_for_status()
            observed |= _span_names(trace.json())
            if required <= observed:
                _out(
                    "OK: required spans observed: "
                    + ", ".join(sorted(required))
                )
                return 0

    missing = sorted(required - observed)
    _out(
        "FAIL: missing required spans: "
        + ", ".join(missing)
        + "; observed: "
        + ", ".join(sorted(observed)),
        error=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
