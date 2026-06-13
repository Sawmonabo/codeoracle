#!/usr/bin/env python3
"""Preflight probe for vLLM's OpenAI-compatible API surface."""

import argparse
import asyncio
import os
import sys
from typing import Any

import httpx


def _out(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(message + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("QWEN_MODEL_ID", "Qwen/Qwen2.5-32B-Instruct"),
    )
    return parser


def _has_embedding(body: dict[str, Any]) -> bool:
    data = body.get("data")
    if not isinstance(data, list) or not data:
        return False
    embedding = data[0].get("embedding")
    return isinstance(embedding, list) and bool(embedding)


async def _main() -> int:
    args = _parser().parse_args()
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=10.0,
    ) as client:
        response = await client.post(
            "/embeddings",
            json={"model": args.model, "input": "hello"},
        )
        response.raise_for_status()
        if not _has_embedding(response.json()):
            _out(
                "FAIL: /v1/embeddings did not return an embedding",
                error=True,
            )
            return 1
    _out("OK: vLLM returned an OpenAI-compatible embedding response")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
