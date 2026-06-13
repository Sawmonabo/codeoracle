"""
Embed indexer worker entrypoint — `python -m codeoracle.workers.embed_indexer`.
"""

import asyncio
import logging
import signal

from codeoracle.adapters.llm.embeddings import EmbeddingsProviderFactory
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import load_valkey_config
from codeoracle.core.config import get_settings
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer

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
    provider = EmbeddingsProviderFactory.build(settings)

    if provider.dim != settings.embedding.dim:
        _LOG.error(
            "embeddings provider dim=%d but EmbeddingConfig.dim=%d — "
            "refusing to start",
            provider.dim,
            settings.embedding.dim,
        )
        return

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # G14: EmbedIndexerConsumer holds the EmbeddingConfig and dispatches
    # chunker_for(file_path) per message — code files get LineChunker,
    # doc files get ParagraphChunker. No chunker instance is constructed
    # here at startup; the consumer builds one per message.
    consumer = EmbedIndexerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(
            max_attempts=3,
        ),
        consumer_name=f"embed-{settings.worker_instance_id}",
        stop_event=stop_event,
        embeddings_provider=provider,
        embedding_config=settings.embedding,
        db_session_factory=db_factory,
    )

    _LOG.info("embed-indexer worker starting")
    try:
        await consumer.run()
    finally:
        # If the provider is OpenAIEmbeddingsProvider, close its httpx client.
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    _LOG.info("embed-indexer worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
