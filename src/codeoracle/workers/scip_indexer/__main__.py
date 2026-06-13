"""
SCIP indexer worker entrypoint — `python -m codeoracle.workers.scip_indexer`.
"""

import asyncio
import logging
import signal

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import load_valkey_config
from codeoracle.core.config import get_settings
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer

_LOG = logging.getLogger(__name__)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    queue_client = ValkeyQueueClient(load_valkey_config())
    await queue_client.start()
    # Plan 3 / Plan 5 contract: make_async_engine() reads Settings internally
    # via get_settings(); make_async_session_factory(engine) binds the factory.
    engine = make_async_engine()
    db_factory = make_async_session_factory(engine)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    consumer = ScipIndexerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(
            max_attempts=2,
        ),
        consumer_name=f"scip-{settings.worker_instance_id}",
        stop_event=stop_event,
        scip_client=ScipClient(),
        parser=ScipIndexParser(),
        db_session_factory=db_factory,
    )

    _LOG.info("scip-indexer worker starting")
    await consumer.run()
    _LOG.info("scip-indexer worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
