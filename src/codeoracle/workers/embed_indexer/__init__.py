"""Embed indexer worker — per-file embedding extraction with internal chunking.

Consumes `indexing.embed` stream; one message per (snapshot, file); writes
N chunk rows to `embeddings` + one outcome row per consumed message.
"""

from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage

__all__ = ["EmbedIndexerConsumer", "EmbedIndexerMessage"]
