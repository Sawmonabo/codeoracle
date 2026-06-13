"""Tests for codeoracle.utils.async_helpers."""

import asyncio

import pytest

from codeoracle.utils.async_helpers import (
    gather_with_concurrency,
    wait_for_with_default,
)


class TestGatherWithConcurrency:
    @pytest.mark.asyncio
    async def test_returns_results_in_input_order(self) -> None:
        async def slow(i: int, delay: float) -> int:
            await asyncio.sleep(delay)
            return i

        results = await gather_with_concurrency(
            2,
            slow(0, 0.02),
            slow(1, 0.01),
            slow(2, 0.0),
        )
        assert results == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_caps_concurrent_executions(self) -> None:
        current = 0
        peak = 0
        lock = asyncio.Lock()

        async def track() -> None:
            nonlocal current, peak
            async with lock:
                current += 1
                peak = max(peak, current)
            await asyncio.sleep(0.01)
            async with lock:
                current -= 1

        await gather_with_concurrency(2, *(track() for _ in range(10)))
        assert peak <= 2

    @pytest.mark.asyncio
    async def test_rejects_zero_or_negative_limit(self) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            await gather_with_concurrency(0, asyncio.sleep(0))

        with pytest.raises(ValueError, match="limit must be >= 1"):
            await gather_with_concurrency(-1, asyncio.sleep(0))


class TestWaitForWithDefault:
    @pytest.mark.asyncio
    async def test_returns_value_when_completes(self) -> None:
        async def fast() -> str:
            return "ok"

        result = await wait_for_with_default(
            fast(), timeout_s=0.1, default="fallback"
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_returns_default_on_timeout(self) -> None:
        async def slow() -> str:
            await asyncio.sleep(0.1)
            return "never"

        result = await wait_for_with_default(
            slow(), timeout_s=0.01, default="fallback"
        )
        assert result == "fallback"
