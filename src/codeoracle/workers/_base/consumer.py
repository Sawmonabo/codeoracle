"""WorkerConsumer — generic async polling loop with ACK/NACK/DLQ.

Subclasses set `stream`, `group`, `consumer_name`, `message_type`, and
implement `handle(msg)`. They DON'T touch the queue client directly —
the base class owns the lifecycle. This is the structural enforcement
of "workers consume from queues; queues are an adapter; workers don't
know which queue implementation they're behind."

Lifecycle:
  1. Construct with: queue_client, retry_policy, structured_logger, stop_event
  2. await consumer.run()  — long-running, polls forever
  3. Signal: stop_event.set() OR send SIGTERM (caller wires this)
  4. Loop exits cleanly: drains in-flight handlers, returns.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import ClassVar, Protocol, cast

from pydantic import ValidationError

from codeoracle.adapters.valkey.client import ConsumedMessage
from codeoracle.core.exceptions import (
    TerminalWorkerError,
    TransientWorkerError,
    WorkerError,
)
from codeoracle.workers._base.message import RetryPolicy, WorkerMessage

logger = logging.getLogger(__name__)


class QueueClientProtocol(Protocol):
    async def reclaim_stale(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int,
    ) -> list[ConsumedMessage]: ...

    async def read_pending(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[ConsumedMessage]: ...

    async def ack(self, stream: str, group: str, message_id: str) -> None: ...

    async def dead_letter(
        self,
        *,
        stream: str,
        message_id: str,
        payload: dict[str, object],
        error: str,
    ) -> None: ...


class WorkerConsumer[TMessage: WorkerMessage](ABC):
    """Abstract base — subclasses provide stream/group/message_type +
    a `handle(msg)` coroutine. The base class owns the polling loop.

    Class-level config (subclasses override):
      stream: Valkey stream name (use the constant from core.streams).
      group: consumer-group name (use the constant from core.streams).
      message_type: the Pydantic WorkerMessage subclass this consumer expects.
      count_per_poll: max messages per XREADGROUP call.
      block_ms: how long XREADGROUP blocks waiting for new messages.

    Instance attributes:
      consumer_name: this consumer's unique name within the group.
                     Default: "<group>-<pid>" so each replica gets a
                     distinct name automatically.
      reclaim_min_idle_ms: if set, the run-loop reclaims PEL entries idle
                     longer than this (XAUTOCLAIM) each poll, advancing their
                     delivered_count so the RetryPolicy ladder can reach
                     dead-letter. MUST exceed the worst-case handle() duration
                     — otherwise a still-running handle is re-claimed, spawning
                     a duplicate extraction (idempotent, but wasteful: a
                     "re-claim stampede" burning worker slots + DB conns).
                     Leave None to DISABLE reclaim — correct only when the
                     worker has another crash-recovery path (e.g. the reindex
                     orchestrator's advisory-lock + stale-APPLYING sweep).
    """

    stream: ClassVar[str]
    group: ClassVar[str]
    message_type: ClassVar[type[WorkerMessage]]
    count_per_poll: ClassVar[int] = 10
    block_ms: ClassVar[int] = 5000

    def __init__(
        self,
        *,
        queue_client: QueueClientProtocol,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        reclaim_min_idle_ms: int | None = None,
    ) -> None:
        self._queue = queue_client
        self._retry = retry_policy
        self._consumer_name = consumer_name
        self._stop = stop_event
        self._reclaim_min_idle_ms = reclaim_min_idle_ms

    @abstractmethod
    async def handle(self, message: TMessage) -> None:
        """Subclass-defined per-message handler.

        Raise TransientWorkerError → consumer will NACK; retry budget
        permitting, the message stays in the PEL and the consumer's reclaim
        step (XAUTOCLAIM) hands it back.

        Raise TerminalWorkerError → consumer dead-letters immediately
        (no retry; the failure is fundamentally not transient).

        Raise any other exception → treated as transient by default
        (defensive); use the explicit exception types to express intent.
        """

    async def run(self) -> None:
        """Poll forever until `stop_event` is set."""
        logger.info(
            "worker_starting",
            extra={
                "stream": self.stream,
                "group": self.group,
                "consumer": self._consumer_name,
            },
        )
        while not self._stop.is_set():
            # Reclaim first: hand stale PEL entries (left by a crashed or
            # stalled consumer) back to this consumer BEFORE reading new work,
            # so a transiently-failed message's delivered_count advances and
            # the RetryPolicy ladder can reach dead-letter. Opt-in per worker
            # via reclaim_min_idle_ms (None → skip; see __init__).
            #
            # The reclaim batch drains fully (each _process_one ACK/NACKs)
            # before read_pending runs, so a consumer never holds reclaimed +
            # new work in-flight at once: the in-flight bound stays
            # count_per_poll x handle, which is why reclaim_min_idle_ms only
            # needs to clear ONE worst-case handle (see JoernIndexerConsumer).
            #
            # NOTE: reclaim guarantees eventual unsticking + dead-letter
            # always;
            # it RESCUES a blip within the *current* reindex barrier only when
            # reclaim_min_idle_ms + one handle < the orchestrator's completion
            # timeout (600s). Operators keeping per-file timeouts well under
            # ~200s stay in that window; slower files dead-letter instead of
            # rescuing in-barrier.
            if self._reclaim_min_idle_ms is not None:
                try:
                    reclaimed = await self._queue.reclaim_stale(
                        stream=self.stream,
                        group=self.group,
                        consumer=self._consumer_name,
                        min_idle_ms=self._reclaim_min_idle_ms,
                        count=self.count_per_poll,
                    )
                except Exception:
                    # A reclaim blip must not kill the loop — log and fall
                    # through to the normal read.
                    logger.exception("worker_reclaim_failed")
                    reclaimed = []
                for raw in reclaimed:
                    await self._process_one(raw)

            try:
                consumed = await self._queue.read_pending(
                    stream=self.stream,
                    group=self.group,
                    consumer=self._consumer_name,
                    count=self.count_per_poll,
                    block_ms=self.block_ms,
                )
            except Exception:
                # Queue-level error — log and back off briefly. Plan 5's
                # WorkerConsumer doesn't retry the *poll itself*; Valkey
                # blips recover on the next loop iteration.
                logger.exception("worker_poll_failed")
                await asyncio.sleep(1.0)
                continue

            for raw in consumed:
                await self._process_one(raw)
        logger.info(
            "worker_stopping",
            extra={"consumer": self._consumer_name},
        )

    async def _process_one(self, raw: ConsumedMessage) -> None:
        """Decode + dispatch + ACK/NACK/DLQ a single message."""
        # 1) Decode + validate against the subclass's message_type.
        try:
            message_type = cast(type[TMessage], self.message_type)
            message = message_type.model_validate(raw.payload)
        except ValidationError as exc:
            # Schema mismatch is terminal — the producer sent garbage.
            await self._dead_letter(raw, f"Schema validation failed: {exc}")
            return

        # 2) Dispatch — handler raises domain errors we translate to
        #    ack/retry/DLQ outcomes.
        try:
            await self.handle(message)
        except TerminalWorkerError as exc:
            await self._dead_letter(raw, f"Terminal failure: {exc}")
            return
        except TransientWorkerError as exc:
            await self._handle_transient(raw, exc)
            return
        except WorkerError as exc:
            # Default classification: treat unclassified WorkerError as
            # transient (safer than dead-lettering on a programmer's
            # ambiguous exception type).
            await self._handle_transient(raw, exc)
            return
        except Exception as exc:
            # Truly unexpected — log + treat transient. If this keeps
            # firing the retry budget will dead-letter it.
            logger.exception("worker_handler_unexpected_error")
            await self._handle_transient(raw, exc)
            return

        # 3) Success — ACK.
        await self._queue.ack(self.stream, self.group, raw.id)

    async def _handle_transient(
        self, raw: ConsumedMessage, error: Exception
    ) -> None:
        """Retry-or-dead-letter based on delivered_count + RetryPolicy."""
        if not self._retry.should_retry(raw.delivered_count):
            await self._dead_letter(
                raw,
                "Retry budget exhausted "
                f"({raw.delivered_count} attempts): {error}",
            )
            return
        # Leave in PEL — do NOT ACK. The run-loop's reclaim step
        # (reclaim_stale → XAUTOCLAIM, for workers that set
        # reclaim_min_idle_ms) hands this entry back once it has idled past the
        # min-idle gate, advancing delivered_count so a later attempt trips
        # should_retry → dead-letter. Workers WITHOUT reclaim configured rely
        # on their own recovery path (see WorkerConsumer.reclaim_min_idle_ms).
        logger.warning(
            "worker_transient_failure",
            extra={
                "stream": self.stream,
                "message_id": raw.id,
                "attempt": raw.delivered_count,
                "error": str(error),
            },
        )

    async def _dead_letter(
        self, raw: ConsumedMessage, error_summary: str
    ) -> None:
        """Copy to DLQ + ACK the producer-stream entry."""
        logger.error(
            "worker_dead_letter",
            extra={
                "stream": self.stream,
                "message_id": raw.id,
                "delivered_count": raw.delivered_count,
                "error": error_summary,
            },
        )
        await self._queue.dead_letter(
            stream=self.stream,
            message_id=raw.id,
            payload=raw.payload,
            error=error_summary,
        )
        await self._queue.ack(self.stream, self.group, raw.id)
