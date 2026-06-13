"""Run the traces ingest worker."""

import asyncio
import signal
from contextlib import suppress

from codeoracle.workers.traces_ingest.wiring import wire_consumer


async def amain() -> None:
    consumer, cleanup = await wire_consumer()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, consumer.stop)
    try:
        await consumer.run()
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(amain())
