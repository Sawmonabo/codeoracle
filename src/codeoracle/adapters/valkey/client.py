"""ValkeyQueueClient — async wrapper exposing the four operations workers
need: enqueue, read_pending, ack, dead_letter.

Why an adapter at all? `valkey-py` returns bytes for stream fields, exposes
XADD's '*' magic ID, has a separate XGROUP/XREADGROUP/XACK ceremony, and
lacks built-in dead-letter semantics. The adapter:
  - Decodes bytes → str at the boundary (callers get pure str).
  - Forces JSON-serialized payloads (callers pass dicts; we serialize).
  - Bootstraps consumer groups idempotently on first enqueue/read.
  - Implements dead-letter as a paired stream + the producer's XACK
    (a dead-lettered message is acked off the producer stream so it
    won't be re-delivered; the DLQ copy holds the failure context).

The adapter is FREE to swap to `valkey-glide` or `redis-py` — call sites
never know the underlying SDK.

`valkey-py`'s async API is identical to `redis.asyncio`. Caller-side, the
methods are:

    qc = ValkeyQueueClient(config)
    await qc.start()  # initialize pool + ensure groups exist
    msg_id = await qc.enqueue("indexing.joern",
    {"snapshot_sha":"abc1234","file":"src/x.py"})
    msgs = await qc.read_pending( stream="indexing.joern",
        group="joern_indexer", consumer="joern-worker-1", count=10,
        block_ms=5000, ) for m in msgs: try: await dispatch(m.payload)
            await qc.ack("indexing.joern", "joern_indexer", m.id)
        except TerminalWorkerError as exc:
            await qc.dead_letter("indexing.joern", m.id, m.payload, str(exc))
            await qc.ack("indexing.joern", "joern_indexer", m.id)
    await qc.aclose()  # called from worker shutdown handler
"""

import json
from dataclasses import dataclass

import valkey.asyncio as valkey

from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.core.streams import dlq_for


@dataclass(frozen=True)
class ConsumedMessage:
    """A single message fetched from a stream.

    Attributes: id: The Valkey stream entry ID (e.g., "1716123456789-0"). Used
            for XACK + XADD into DLQ.
        stream: Which stream this came from (set by read_pending).
        payload: The original dict the producer passed to enqueue().
        delivered_count: Times this message has been delivered (via
            XPENDING). Used by the consumer to decide retry vs dead-letter.
    """

    id: str
    stream: str
    payload: dict[str, object]
    delivered_count: int


class ValkeyQueueClient:
    """Async queue client over Valkey streams.

    Construct ONCE per process; reuse across calls. `start()` must be
    awaited before any other method (initializes the connection pool
    and ensures consumer groups exist).
    """

    def __init__(self, config: ValkeyConfig) -> None:
        self._config = config
        self._client: valkey.Valkey | None = None
        self._ensured_groups: set[tuple[str, str]] = set()

    async def start(self) -> None:
        """Initialize connection pool. Safe to call once per process."""
        if self._client is not None:
            return
        self._client = valkey.from_url(
            self._config.url,
            max_connections=self._config.pool_max,
            decode_responses=False,  # we decode at the message boundary
        )
        # Ping to fail fast if Valkey unreachable.
        await self._client.ping()

    async def ping(self) -> None:
        """Ping Valkey using the already-started connection pool."""

        client = self._require_client()
        await client.ping()

    async def aclose(self) -> None:
        """Tear down the connection pool. Idempotent."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def enqueue(self, stream: str, payload: dict[str, object]) -> str:
        """XADD a JSON-serialized payload to `stream`. Returns the entry ID."""
        client = self._require_client()
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        # MAXLEN caps the stream so XACKed-but-retained entries can't
        # accumulate across reindexes; approximate=True trims at radix-node
        # boundaries (cheap) and never drops below stream_maxlen, so a
        # correctly-sized cap never evicts the in-flight backlog.
        entry_id = await client.xadd(
            stream,
            {"json": body},
            maxlen=self._config.stream_maxlen,
            approximate=True,
        )
        return (
            entry_id.decode("utf-8")
            if isinstance(entry_id, bytes)
            else entry_id
        )

    async def read_pending(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[ConsumedMessage]:
        """XREADGROUP from `stream` as `consumer` in `group`. Bootstraps
        the group via XGROUP CREATE MKSTREAM on first call.

        Returns at most `count` messages. Blocks up to `block_ms`
        milliseconds for new messages. Returns [] on timeout.
        """
        client = self._require_client()
        await self._ensure_group(stream, group)

        raw = await client.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={
                stream: ">"
            },  # ">" = only new (not yet delivered) messages
            count=count,
            block=block_ms,
        )
        if not raw:
            return []

        # raw shape: [(stream_name_bytes, [(id_bytes, {field_bytes:
        # value_bytes})])]
        out: list[ConsumedMessage] = []
        for stream_name_b, entries in raw:
            stream_name = stream_name_b.decode("utf-8")
            for entry_id_b, fields in entries:
                msg = await self._decode_entry(
                    stream_name, group, entry_id_b, fields
                )
                if msg is not None:
                    out.append(msg)
        return out

    async def reclaim_stale(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int,
    ) -> list[ConsumedMessage]:
        """XAUTOCLAIM PEL entries idle longer than `min_idle_ms` to `consumer`.

        Reclaims messages a crashed-or-stalled consumer left un-ACKed in the
        group's pending list. XAUTOCLAIM atomically (a) reassigns each stale
        entry to `consumer` and (b) increments its delivery counter — so the
        returned ConsumedMessage carries an ADVANCED `delivered_count`. That
        advancing counter is what lets `RetryPolicy.should_retry` drive the
        retry-then-dead-letter ladder: XREADGROUP's ">" only ever reads
        never-delivered messages, so WITHOUT this reclaim a transiently-failed
        entry sits in the PEL with `delivered_count` frozen at 1 forever, the
        ladder never advances, and the retry-exhaustion dead-letter is
        unreachable (the message is marooned).

        Bootstraps the group (XAUTOCLAIM errors on a missing group) — required
        because the consumer's run-loop reclaims BEFORE its first read_pending.
        Reclaims one batch per call (the XAUTOCLAIM cursor is not looped): the
        min-idle gate bounds the eligible set, and the next poll continues as
        more entries age past `min_idle_ms`. Returns [] when nothing is stale.
        """
        client = self._require_client()
        await self._ensure_group(stream, group)

        # XAUTOCLAIM <stream> <group> <consumer> <min-idle-ms> <start> COUNT n
        #   → (next_cursor, [(id, {field: value}), ...], deleted_ids).
        # We ignore the cursor (one batch per poll) and deleted_ids (entries
        # already evicted from the stream — nothing left to reprocess).
        _cursor, entries, _deleted = await client.xautoclaim(
            name=stream,
            groupname=group,
            consumername=consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )

        out: list[ConsumedMessage] = []
        for entry_id_b, fields in entries:
            msg = await self._decode_entry(stream, group, entry_id_b, fields)
            if msg is not None:
                out.append(msg)
        return out

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        """
        XACK — removes the message from the consumer-group's pending list.
        """
        client = self._require_client()
        await client.xack(stream, group, message_id)

    async def dead_letter(
        self,
        stream: str,
        message_id: str,
        payload: dict[str, object],
        error: str,
    ) -> None:
        """Copy the failed message to `<stream>.dlq` with the error
        context, then ACK it off the producer stream so it won't be
        re-delivered.

        Workers should call this when an error is terminal (retries
        exhausted, or the error is fundamentally caller-bug). NEVER call
        this for transient failures — leave them in the PEL for the
        consumer's reclaim step to retry.
        """
        client = self._require_client()
        dlq = dlq_for(stream)
        # DLQ reuses the producer cap (stream_maxlen). Unlike the producer
        # stream it has no live-work floor — entries are sparse terminal
        # failures — so the producer-sized cap merely over-provisions it.
        await client.xadd(
            dlq,
            {
                "original_id": message_id,
                "original_stream": stream,
                "payload": json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ),
                "error": error,
            },
            maxlen=self._config.stream_maxlen,
            approximate=True,
        )
        # ACK the original to clear it from the producer-stream PEL.
        # The DLQ copy is the authoritative record of the failure.

    async def stream_length(self, stream: str) -> int:
        """XLEN — current entry count of `stream` (NFR D4-4 observability).

        Read-only and side-effect-free: a metrics scrape must never mutate
        queue state. XLEN of a not-yet-created stream returns 0 (Valkey treats
        a missing key as an empty stream), so this is safe to call before any
        producer has XADDed. Consumed only by the API's queue-metrics refresher
        (Plan 11) to export the `codeoracle_valkey_stream_length` gauge.
        """
        client = self._require_client()
        return int(await client.xlen(stream))

    async def pending_count(self, stream: str, group: str) -> int:
        """XPENDING summary count — size of `group`'s pending-entries list
        (PEL) on `stream` (NFR D4-4 — observability of D4-1's reclaim backlog).

        Read-only and side-effect-free: unlike `read_pending` / `reclaim_stale`
        it deliberately does NOT bootstrap the group via `_ensure_group` — a
        metrics scrape must not create consumer groups as a side effect. A
        not-yet-created group raises NOGROUP, which we map to 0 (no group means
        no pending backlog yet), mirroring how `stream_length` treats a missing
        stream as empty. Consumed only by the API's queue-metrics refresher
        (Plan 11) to export the `codeoracle_valkey_pending_entries` gauge.
        """
        client = self._require_client()
        try:
            summary = await client.xpending(stream, group)
        except valkey.ResponseError as exc:
            # NOGROUP — the consumer group hasn't been created yet (no worker
            # has bootstrapped it). Any other ResponseError is unexpected.
            if "NOGROUP" in str(exc):
                return 0
            raise
        # Summary shape: {"pending": int, "min": id, "max": id, "consumers":
        # […]}.
        return int(summary["pending"])

    async def _decode_entry(
        self,
        stream: str,
        group: str,
        entry_id_b: bytes,
        fields: dict[bytes, bytes],
    ) -> ConsumedMessage | None:
        """Decode one XREADGROUP / XAUTOCLAIM entry into a ConsumedMessage.

        Shared by `read_pending` (new messages) and `reclaim_stale` (reclaimed
        PEL messages) so the malformed-payload guard and `delivered_count`
        sourcing can never diverge between the two read paths. Returns None
        when the payload is unparsable — in that case the entry has already
        been dead-lettered + ACKed (so it can't loop in the PEL) and the caller
        skips it.
        """
        client = self._require_client()
        entry_id = entry_id_b.decode("utf-8")
        body = fields.get(b"json", b"{}").decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            # Malformed payload — dead-letter + ACK immediately so it doesn't
            # sit in the PEL forever. DLQ reuses the producer cap
            # (stream_maxlen); see dead_letter for why that floor is safe here.
            await client.xadd(
                dlq_for(stream),
                {
                    "original_id": entry_id,
                    "raw": body,
                    "error": f"JSON decode failed: {exc}",
                },
                maxlen=self._config.stream_maxlen,
                approximate=True,
            )
            await client.xack(stream, group, entry_id)
            return None

        # XPENDING gives the delivery count; fetch lazily per-message.
        delivered_count = await self._delivered_count(stream, group, entry_id)
        return ConsumedMessage(
            id=entry_id,
            stream=stream,
            payload=payload,
            delivered_count=delivered_count,
        )

    async def _ensure_group(self, stream: str, group: str) -> None:
        """XGROUP CREATE ... MKSTREAM — idempotent. Skip if already created."""
        if (stream, group) in self._ensured_groups:
            return
        client = self._require_client()
        try:
            # MKSTREAM creates the stream if absent — works even for the
            # first publisher who hasn't XADDed yet.
            await client.xgroup_create(
                name=stream, groupname=group, id="0-0", mkstream=True
            )
        except valkey.ResponseError as exc:
            # BUSYGROUP — already exists. Any other error is unexpected.
            if "BUSYGROUP" not in str(exc):
                raise
        self._ensured_groups.add((stream, group))

    async def _delivered_count(
        self, stream: str, group: str, message_id: str
    ) -> int:
        """XPENDING for one message — returns 0 if not in pending (shouldn't
        happen for a just-fetched message but defensively return 0)."""
        client = self._require_client()
        pending = await client.xpending_range(
            name=stream,
            groupname=group,
            min=message_id,
            max=message_id,
            count=1,
        )
        if not pending:
            return 0
        # Each row: {"message_id", "consumer", "time_since_delivered",
        # "times_delivered"}
        return int(pending[0]["times_delivered"])

    def _require_client(self) -> valkey.Valkey:
        if self._client is None:
            raise RuntimeError(
                "ValkeyQueueClient not started — "
                "call await client.start() first."
            )
        return self._client
