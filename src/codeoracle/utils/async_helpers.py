"""Small async helpers used across the platform.

Utility-shaped: no business logic, no I/O wrappers, no service-layer
dependencies. Safe to import from any layer.
"""

import asyncio
from collections.abc import Awaitable, Coroutine


async def gather_with_concurrency[T](
    limit: int,
    *coros: Coroutine[None, None, T],
) -> list[T]:
    """Run coroutines concurrently with a bound on parallelism.

    `asyncio.gather` has no concurrency limit; this wraps it with a
    `Semaphore` for parallel-but-bounded execution. Results return in
    input order regardless of completion order.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    semaphore = asyncio.Semaphore(limit)

    async def _bounded(coro: Coroutine[None, None, T]) -> T:
        async with semaphore:
            return await coro

    return await asyncio.gather(*[_bounded(c) for c in coros])


async def wait_for_with_default[T](
    awaitable: Awaitable[T],
    timeout_s: float,
    default: T,
) -> T:
    """Like `asyncio.wait_for`.

    Returns `default` on timeout instead of raising.

    Used at adapter boundaries where a missing upstream response should
    degrade gracefully rather than cascade an exception. Callers that
    need the raising behavior should call `asyncio.wait_for` directly.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    except TimeoutError:
        return default
