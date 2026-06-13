"""OpenGrep runner worker entrypoint."""

import asyncio
import logging
import signal

from codeoracle.adapters.opengrep import DEFAULT_RULES_DIR
from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import load_valkey_config
from codeoracle.core.config import get_settings
from codeoracle.core.exceptions import OpengrepRunError
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer

_LOG = logging.getLogger(__name__)


async def amain() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    queue_client = ValkeyQueueClient(load_valkey_config())
    await queue_client.start()
    engine = make_async_engine()
    db_factory = make_async_session_factory(engine)

    runner = OpengrepRunner(rules_dir=DEFAULT_RULES_DIR)
    try:
        version = await runner.verify_binary()
    except OpengrepRunError as exc:
        _LOG.error(
            "opengrep binary missing/unusable; refusing to start: %s", exc
        )
        return
    _LOG.info("opengrep binary OK: %s", version)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    consumer = OpengrepRunnerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(max_attempts=3),
        consumer_name=f"opengrep-{settings.worker_instance_id}",
        stop_event=stop_event,
        runner=runner,
        db_session_factory=db_factory,
    )

    _LOG.info("opengrep-runner worker starting")
    try:
        await consumer.run()
    finally:
        await queue_client.aclose()
        await engine.dispose()
    _LOG.info("opengrep-runner worker stopped")


if __name__ == "__main__":
    asyncio.run(amain())
