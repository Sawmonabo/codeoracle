"""Trace domain models for Tempo ingestion and lookup."""

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SpanKind = Literal[
    "unspecified", "internal", "server", "client", "producer", "consumer"
]
SpanStatusCode = Literal["unset", "ok", "error"]
TRACE_ID_BYTES = 16
SPAN_ID_BYTES = 8


class Span(BaseModel):
    """Normalized OpenTelemetry span."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: bytes
    span_id: bytes
    parent_span_id: bytes | None = None
    service_name: str
    operation_name: str
    kind: SpanKind = "internal"
    start_unix_nano: int = Field(ge=0)
    end_unix_nano: int = Field(ge=0)
    status_code: SpanStatusCode = "unset"
    status_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    scope_name: str | None = None
    scope_version: str | None = None

    @field_validator("trace_id")
    @classmethod
    def _trace_id_is_16_bytes(cls, value: bytes) -> bytes:
        if len(value) != TRACE_ID_BYTES:
            raise ValueError("trace_id must be 16 bytes")
        return value

    @field_validator("span_id", "parent_span_id")
    @classmethod
    def _span_ids_are_8_bytes(cls, value: bytes | None) -> bytes | None:
        if value is not None and len(value) != SPAN_ID_BYTES:
            raise ValueError("span_id and parent_span_id must be 8 bytes")
        return value

    @model_validator(mode="after")
    def _validate_span(self) -> Span:
        if self.end_unix_nano < self.start_unix_nano:
            raise ValueError("end_unix_nano must be >= start_unix_nano")
        if self.status_code == "error" and self.status_message is None:
            raise ValueError(
                "status_message is required when status_code=='error'"
            )
        return self

    @property
    def duration_nanos(self) -> int:
        return self.end_unix_nano - self.start_unix_nano

    @property
    def duration(self) -> timedelta:
        return timedelta(microseconds=self.duration_nanos / 1_000)


TraceSpan = Span


class SpanFilter(BaseModel):
    """Filter for trace-id lookups or service/time-window lookups."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: bytes | None = None
    service_name: str | None = None
    route: str | None = None
    start_unix_nano: int | None = Field(default=None, ge=0)
    end_unix_nano: int | None = Field(default=None, ge=0)
    limit: int = Field(default=100, ge=1)

    @field_validator("trace_id")
    @classmethod
    def _trace_id_is_16_bytes(cls, value: bytes | None) -> bytes | None:
        if value is not None and len(value) != TRACE_ID_BYTES:
            raise ValueError("trace_id must be 16 bytes")
        return value

    @model_validator(mode="after")
    def _validate_filter_mode(self) -> SpanFilter:
        trace_mode = self.trace_id is not None
        has_start = self.start_unix_nano is not None
        has_end = self.end_unix_nano is not None

        if self.route is not None and self.service_name is None:
            raise ValueError("route requires service_name")
        if has_start != has_end:
            raise ValueError(
                "start_unix_nano and end_unix_nano must be provided together"
            )
        if has_start and has_end and self.end_unix_nano < self.start_unix_nano:
            raise ValueError("end_unix_nano must be >= start_unix_nano")

        service_mode = self.service_name is not None and has_start and has_end
        if trace_mode == service_mode:
            raise ValueError(
                "exactly one of trace_id or service_name with "
                "start_unix_nano/end_unix_nano is required"
            )
        return self

    def is_trace_lookup(self) -> bool:
        return self.trace_id is not None

    def is_service_window(self) -> bool:
        return (
            self.service_name is not None
            and self.start_unix_nano is not None
            and self.end_unix_nano is not None
        )


class TimestampCursor(BaseModel):
    """Cursor for timestamp-based continuous ingestion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    last_ingested_unix_nano: int = Field(ge=0)
    overlap_seconds: int = Field(default=10, ge=0)

    def fetch_window_lower_bound_nanos(self) -> int:
        return max(
            0,
            self.last_ingested_unix_nano
            - self.overlap_seconds * 1_000_000_000,
        )


class IngestStats(BaseModel):
    """Structured telemetry payload for a continuous-ingest tick."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    cursor_before_unix_nano: int = Field(ge=0)
    cursor_after_unix_nano: int = Field(ge=0)
    spans_fetched: int = Field(ge=0)
    spans_persisted: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_stats(self) -> IngestStats:
        if self.cursor_after_unix_nano < self.cursor_before_unix_nano:
            raise ValueError("cursor regressed")
        if self.spans_persisted > self.spans_fetched:
            raise ValueError("spans_persisted must be <= spans_fetched")
        return self

    @property
    def dedup_ratio(self) -> float:
        if self.spans_fetched == 0:
            return 0.0
        return (self.spans_fetched - self.spans_persisted) / self.spans_fetched


class TraceSearchWindow(BaseModel):
    """Datetime-oriented Tempo search window used by adapter boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_name: str | None = None
    route: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = Field(default=100, ge=1)
