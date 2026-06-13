"""SCIP indexer worker — project-mode extraction.

Consumes `indexing.scip` stream; one message per snapshot reindex; writes
N `SnapshotFileOutcome` rows + N `:Definition`/`:Reference` subgraphs to AGE.
"""

from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage

__all__ = ["ScipIndexerConsumer", "ScipIndexerMessage"]
