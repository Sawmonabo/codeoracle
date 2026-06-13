"""Valkey adapter — the platform's queue client.

Plan 5: workers and IndexingService talk to Valkey ONLY through
ValkeyQueueClient. Direct `valkey-py` / `redis-py` imports outside
this package are forbidden (enforced by ruff banned-api in Task 12 Step 4).
"""

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig

__all__ = ["ValkeyConfig", "ValkeyQueueClient"]
