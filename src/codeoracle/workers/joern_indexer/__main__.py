"""
Joern indexer worker entrypoint — `python -m codeoracle.workers.joern_indexer`.
"""

import asyncio

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.workers._base.wiring import (
    build_worker_context,
    teardown_worker_context,
)
from codeoracle.workers.joern_indexer.consumer import JoernIndexerConsumer


async def amain() -> None:
    ctx = await build_worker_context(consumer_name_prefix="joern_indexer")
    joern_client = JoernClient(JoernConfig.model_validate({}))
    parser = JoernCpgParser()
    consumer = JoernIndexerConsumer(
        queue_client=ctx.queue_client,
        retry_policy=ctx.retry_policy,
        consumer_name=ctx.consumer_name,
        stop_event=ctx.stop_event,
        joern_client=joern_client,
        parser=parser,
        db_session_factory=ctx.db_session_factory,
    )
    try:
        await consumer.run()
    finally:
        await teardown_worker_context(ctx)


if __name__ == "__main__":
    asyncio.run(amain())
