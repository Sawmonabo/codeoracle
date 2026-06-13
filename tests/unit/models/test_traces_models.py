"""Plan 7 Task 2: domain-model unit tests.

These pin the OTel-semantic-convention field names and the SpanFilter
mutual-exclusion invariant. Downstream code (TracesRepository,
TempoClient, TracesIngestConsumer) depends on these exact shapes;
breaking them is a cross-plan blast radius. Hence: tests first.
"""

from typing import get_args

import pytest

from codeoracle.models.traces import (
    IngestStats,
    Span,
    SpanFilter,
    SpanKind,
    SpanStatusCode,
    TimestampCursor,
)

# -----------------------------------------------------------------------------
# Span
# -----------------------------------------------------------------------------


def _valid_span_data(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": b"\x01" * 16,
        "span_id": b"\x02" * 8,
        "parent_span_id": b"\x03" * 8,
        "service_name": "api",
        "operation_name": "POST /v1/query",
        "kind": "server",
        "start_unix_nano": 1_700_000_000_000_000_000,
        "end_unix_nano": 1_700_000_000_050_000_000,  # +50ms
        "status_code": "ok",
        "status_message": None,
        "attributes": {"http.route": "/v1/query", "http.method": "POST"},
        "scope_name": "codeoracle.api",
        "scope_version": "0.1.0",
    }
    base.update(overrides)
    return base


def _span(**overrides: object) -> Span:
    return Span.model_validate(_valid_span_data(**overrides))


def _assign_for_validation(
    instance: object,
    field_name: str,
    value: object,
) -> None:
    setattr(instance, field_name, value)


def test_span_minimal_construction_succeeds() -> None:
    span = _span()
    assert span.trace_id == b"\x01" * 16
    assert len(span.span_id) == 8
    assert span.kind == "server"
    assert span.attributes["http.route"] == "/v1/query"


def test_span_kind_must_be_in_enum() -> None:
    with pytest.raises(ValueError, match="kind"):
        Span.model_validate(_valid_span_data(kind="banana"))


def test_span_status_code_must_be_in_enum() -> None:
    with pytest.raises(ValueError, match="status_code"):
        Span.model_validate(_valid_span_data(status_code="banana"))


def test_span_trace_id_must_be_16_bytes() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        Span.model_validate(_valid_span_data(trace_id=b"\x01" * 8))


def test_span_span_id_must_be_8_bytes() -> None:
    with pytest.raises(ValueError, match="8 bytes"):
        Span.model_validate(_valid_span_data(span_id=b"\x02" * 16))


def test_span_parent_span_id_optional() -> None:
    # Root span has no parent — None is the legal absence.
    span = _span(parent_span_id=None)
    assert span.parent_span_id is None


def test_span_parent_span_id_when_present_must_be_8_bytes() -> None:
    with pytest.raises(ValueError, match="8 bytes"):
        Span.model_validate(_valid_span_data(parent_span_id=b"\x03" * 4))


def test_span_end_must_be_at_or_after_start() -> None:
    with pytest.raises(
        ValueError, match="end_unix_nano must be >= start_unix_nano"
    ):
        Span.model_validate(
            _valid_span_data(
                start_unix_nano=1_700_000_000_000_000_000,
                end_unix_nano=1_700_000_000_000_000_000 - 1,
            )
        )


def test_span_attributes_default_to_empty_dict() -> None:
    span = _span(attributes={})
    assert span.attributes == {}


def test_span_is_immutable() -> None:
    span = _span()
    with pytest.raises(ValueError, match="frozen"):
        _assign_for_validation(span, "service_name", "different")


def test_span_status_message_required_when_status_is_error() -> None:
    """OTel convention: status_message is human-readable description of
    why a span is in an error state. Allowing status_code=error +
    status_message=None makes log aggregation lossy. Reject."""
    with pytest.raises(
        ValueError,
        match="status_message is required when status_code=='error'",
    ):
        Span.model_validate(
            _valid_span_data(status_code="error", status_message=None)
        )


def test_span_status_message_unused_when_status_is_ok() -> None:
    # OK + empty message is the common case; no validator complaint.
    span = _span(status_code="ok", status_message=None)
    assert span.status_code == "ok"


def test_span_duration_helper() -> None:
    span = _span(
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=1_700_000_000_050_000_000,  # +50ms
    )
    assert span.duration_nanos == 50_000_000
    assert span.duration.total_seconds() == pytest.approx(0.050)


# -----------------------------------------------------------------------------
# SpanFilter
# -----------------------------------------------------------------------------


def test_span_filter_by_trace_id() -> None:
    f = SpanFilter(trace_id=b"\x01" * 16)
    assert f.is_trace_lookup()
    assert not f.is_service_window()


def test_span_filter_by_service_and_window() -> None:
    f = SpanFilter(
        service_name="api",
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=1_700_000_001_000_000_000,
    )
    assert f.is_service_window()
    assert not f.is_trace_lookup()


def test_span_filter_rejects_mixed_modes() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        SpanFilter(
            trace_id=b"\x01" * 16,
            service_name="api",
            start_unix_nano=1_700_000_000_000_000_000,
            end_unix_nano=1_700_000_001_000_000_000,
        )


def test_span_filter_rejects_no_mode() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        SpanFilter()


def test_span_filter_service_window_requires_both_bounds() -> None:
    with pytest.raises(ValueError, match=r"start_unix_nano.*end_unix_nano"):
        SpanFilter(service_name="api", start_unix_nano=1)  # no end
    with pytest.raises(ValueError, match=r"start_unix_nano.*end_unix_nano"):
        SpanFilter(service_name="api", end_unix_nano=1)  # no start


def test_span_filter_service_window_end_after_start() -> None:
    with pytest.raises(
        ValueError, match="end_unix_nano must be >= start_unix_nano"
    ):
        SpanFilter(
            service_name="api",
            start_unix_nano=2_000_000_000_000_000_000,
            end_unix_nano=1_000_000_000_000_000_000,
        )


def test_span_filter_route_attribute_optional() -> None:
    f = SpanFilter(
        service_name="api",
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=1_700_000_001_000_000_000,
        route="/v1/query",
    )
    assert f.route == "/v1/query"


def test_span_filter_route_requires_service_window_mode() -> None:
    """route is a sub-filter on top of service_name+window — it cannot
    appear in trace_id mode."""
    with pytest.raises(ValueError, match="route requires service_name"):
        SpanFilter(trace_id=b"\x01" * 16, route="/v1/query")


# -----------------------------------------------------------------------------
# TimestampCursor (CursorStore[TimestampCursor])
# -----------------------------------------------------------------------------


def test_timestamp_cursor_default_overlap() -> None:
    c = TimestampCursor(last_ingested_unix_nano=1_700_000_000_000_000_000)
    assert c.overlap_seconds == 10  # default — re-poll last 10s on next tick


def test_timestamp_cursor_overlap_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        TimestampCursor(last_ingested_unix_nano=1, overlap_seconds=-1)


def test_timestamp_cursor_fetch_window_lower_bound() -> None:
    c = TimestampCursor(
        last_ingested_unix_nano=1_700_000_000_000_000_000, overlap_seconds=10
    )
    # Fetch window lower bound is "last_ingested - overlap_seconds"
    assert c.fetch_window_lower_bound_nanos() == 1_700_000_000_000_000_000 - (
        10 * 1_000_000_000
    )


def test_timestamp_cursor_is_frozen() -> None:
    c = TimestampCursor(last_ingested_unix_nano=1)
    with pytest.raises(ValueError, match="frozen"):
        _assign_for_validation(c, "last_ingested_unix_nano", 2)


def test_timestamp_cursor_jsonable_round_trip() -> None:
    c1 = TimestampCursor(
        last_ingested_unix_nano=1_700_000_000_000_000_000, overlap_seconds=15
    )
    blob = c1.model_dump_json()
    c2 = TimestampCursor.model_validate_json(blob)
    assert c1 == c2


# -----------------------------------------------------------------------------
# IngestStats — telemetry payload
# -----------------------------------------------------------------------------


def test_ingest_stats_construction() -> None:
    stats = IngestStats(
        source="tempo",
        cursor_before_unix_nano=1_700_000_000_000_000_000,
        cursor_after_unix_nano=1_700_000_030_000_000_000,
        spans_fetched=42,
        spans_persisted=40,  # 2 duplicates dropped at PK
        elapsed_seconds=0.234,
    )
    assert stats.dedup_ratio == pytest.approx(2 / 42)


def test_ingest_stats_dedup_ratio_zero_when_no_fetch() -> None:
    stats = IngestStats(
        source="tempo",
        cursor_before_unix_nano=1,
        cursor_after_unix_nano=2,
        spans_fetched=0,
        spans_persisted=0,
        elapsed_seconds=0.001,
    )
    assert stats.dedup_ratio == 0.0


def test_ingest_stats_rejects_persisted_greater_than_fetched() -> None:
    with pytest.raises(ValueError, match=r"spans_persisted .* spans_fetched"):
        IngestStats(
            source="tempo",
            cursor_before_unix_nano=1,
            cursor_after_unix_nano=2,
            spans_fetched=10,
            spans_persisted=11,
            elapsed_seconds=0.001,
        )


def test_ingest_stats_rejects_negative_elapsed() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        IngestStats(
            source="tempo",
            cursor_before_unix_nano=1,
            cursor_after_unix_nano=2,
            spans_fetched=0,
            spans_persisted=0,
            elapsed_seconds=-0.1,
        )


def test_ingest_stats_rejects_regressing_cursor() -> None:
    # _validate_stats refuses a cursor that regresses (after < before).
    # The ContinuousConsumer.fetch_batch invariant is "new_cursor MUST be
    # monotonically non-decreasing"; IngestStats enforces it again at
    # log-emission so a buggy consumer cannot quietly persist a regressing
    # cursor to telemetry.
    with pytest.raises(ValueError, match="cursor regressed"):
        IngestStats(
            source="tempo",
            cursor_before_unix_nano=1_700_000_000_000_000_000,
            cursor_after_unix_nano=1_700_000_000_000_000_000 - 1,
            spans_fetched=0,
            spans_persisted=0,
            elapsed_seconds=0.001,
        )


def test_span_kind_and_status_code_are_literal_types() -> None:
    """Type aliases for static analysis — runtime values are constrained
    by the Pydantic Literal[] field validator."""
    # SpanKind / SpanStatusCode are exported for use in services + routers
    assert "server" in get_args(SpanKind)
    assert "error" in get_args(SpanStatusCode)
