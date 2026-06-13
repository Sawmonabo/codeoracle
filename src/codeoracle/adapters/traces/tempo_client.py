"""Tempo HTTP adapter."""

import base64
import binascii
from dataclasses import dataclass
from typing import Any, cast

import httpx

from codeoracle.models.traces import Span, SpanKind, SpanStatusCode

_HTTP_OK = 200
_HTTP_MULTIPLE_CHOICES = 300
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500

_KIND_BY_OTLP_CODE = {
    0: "unspecified",
    1: "internal",
    2: "server",
    3: "client",
    4: "producer",
    5: "consumer",
}
_STATUS_BY_OTLP_CODE = {0: "unset", 1: "ok", 2: "error"}


class TempoError(Exception):
    """Base class for Tempo adapter failures."""


class TempoTransientError(TempoError):
    """Retryable Tempo failure."""


class TempoTerminalError(TempoError):
    """Non-retryable Tempo failure."""


@dataclass(frozen=True, slots=True)
class TraceSummary:
    trace_id: bytes
    root_service_name: str
    root_span_name: str
    start_unix_nano: int
    duration_nanos: int


class TempoClient:
    """Client boundary for Tempo search and trace retrieval."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def search_traces_in_window(
        self,
        *,
        start_unix_nano: int,
        end_unix_nano: int,
        limit: int,
    ) -> list[TraceSummary]:
        payload = await self._get_json(
            "/api/search",
            params={
                "q": "{}",
                "start": str(start_unix_nano),
                "end": str(end_unix_nano),
                "limit": str(limit),
            },
        )
        traces = payload.get("traces", [])
        if not isinstance(traces, list):
            raise TempoTerminalError("Tempo search response missing traces")
        return [_summary_from_wire(item) for item in traces]

    async def get_trace_by_id(self, trace_id: bytes) -> list[Span]:
        trace_id_hex = trace_id.hex()
        payload = await self._get_json(f"/api/v2/traces/{trace_id_hex}")
        return _spans_from_trace_payload(payload)

    async def search(self, **kwargs: Any) -> list[str]:
        summaries = await self.search_traces_in_window(
            start_unix_nano=int(kwargs["start_time"].timestamp() * 1e9),
            end_unix_nano=int(kwargs["end_time"].timestamp() * 1e9),
            limit=kwargs.get("limit", 100),
        )
        return [summary.trace_id.hex() for summary in summaries]

    async def get_trace(self, trace_id: str) -> list[Span]:
        return await self.get_trace_by_id(bytes.fromhex(trace_id))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = await self._client.get(url, params=params)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TempoTransientError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TempoTransientError(str(exc)) from exc

        if not (_HTTP_OK <= response.status_code < _HTTP_MULTIPLE_CHOICES):
            message = f"{response.status_code}: {response.text[:500]}"
            if (
                response.status_code == _HTTP_TOO_MANY_REQUESTS
                or response.status_code >= _HTTP_SERVER_ERROR
            ):
                raise TempoTransientError(message)
            raise TempoTerminalError(message)

        try:
            data = response.json()
        except ValueError as exc:
            raise TempoTerminalError(
                "Tempo returned non-JSON response"
            ) from exc
        if not isinstance(data, dict):
            raise TempoTerminalError("Tempo returned non-object JSON response")
        return data


def _summary_from_wire(item: Any) -> TraceSummary:
    if not isinstance(item, dict):
        raise TempoTerminalError("Tempo trace summary must be an object")
    return TraceSummary(
        trace_id=_decode_otlp_bytes(str(item["traceID"]), expected_len=16),
        root_service_name=str(item.get("rootServiceName") or "unknown"),
        root_span_name=str(item.get("rootTraceName") or "unknown"),
        start_unix_nano=int(item["startTimeUnixNano"]),
        duration_nanos=int(float(item.get("durationMs", 0)) * 1_000_000),
    )


def _spans_from_trace_payload(payload: dict[str, Any]) -> list[Span]:
    trace = payload.get("trace", payload)
    if not isinstance(trace, dict):
        raise TempoTerminalError(
            "Tempo trace payload must contain trace object"
        )
    resource_spans = trace.get("resourceSpans", trace.get("batches", []))
    if not isinstance(resource_spans, list):
        raise TempoTerminalError("Tempo trace resource spans must be a list")

    spans: list[Span] = []
    for resource_span in resource_spans:
        if not isinstance(resource_span, dict):
            raise TempoTerminalError("resourceSpan must be an object")
        resource = resource_span.get("resource", {})
        service_name = _service_name(resource)
        scope_spans = resource_span.get("scopeSpans", [])
        if not isinstance(scope_spans, list):
            raise TempoTerminalError("scopeSpans must be a list")
        for scope_span in scope_spans:
            if not isinstance(scope_span, dict):
                raise TempoTerminalError("scopeSpan must be an object")
            scope = scope_span.get("scope", {})
            scope_name = scope.get("name") if isinstance(scope, dict) else None
            scope_version = (
                scope.get("version") if isinstance(scope, dict) else None
            )
            wire_spans = scope_span.get("spans", [])
            if not isinstance(wire_spans, list):
                raise TempoTerminalError("spans must be a list")
            for wire_span in wire_spans:
                spans.append(
                    _otlp_span_to_domain(
                        wire_span,
                        service_name=service_name,
                        scope_name=scope_name,
                        scope_version=scope_version,
                    )
                )
    return spans


def _service_name(resource: Any) -> str:
    if not isinstance(resource, dict):
        return "unknown"
    attrs = _unwrap_otlp_attributes(resource.get("attributes", []))
    value = attrs.get("service.name")
    return str(value) if value else "unknown"


def _otlp_span_to_domain(
    item: Any,
    *,
    service_name: str,
    scope_name: Any,
    scope_version: Any,
) -> Span:
    if not isinstance(item, dict):
        raise TempoTerminalError("span must be an object")
    status = item.get("status", {})
    if not isinstance(status, dict):
        status = {}
    status_code = cast(
        SpanStatusCode,
        _STATUS_BY_OTLP_CODE.get(int(status.get("code", 0)), "unset"),
    )
    status_message = status.get("message") or None
    parent_span_id = item.get("parentSpanId") or None
    return Span(
        trace_id=_decode_otlp_bytes(str(item["traceId"]), expected_len=16),
        span_id=_decode_otlp_bytes(str(item["spanId"]), expected_len=8),
        parent_span_id=(
            _decode_otlp_bytes(str(parent_span_id), expected_len=8)
            if parent_span_id
            else None
        ),
        service_name=service_name,
        operation_name=str(item["name"]),
        kind=cast(
            SpanKind,
            _KIND_BY_OTLP_CODE.get(int(item.get("kind", 0)), "unspecified"),
        ),
        start_unix_nano=int(item["startTimeUnixNano"]),
        end_unix_nano=int(item["endTimeUnixNano"]),
        status_code=status_code,
        status_message=status_message,
        attributes=_unwrap_otlp_attributes(item.get("attributes", [])),
        scope_name=str(scope_name) if scope_name is not None else None,
        scope_version=(
            str(scope_version) if scope_version is not None else None
        ),
    )


def _unwrap_otlp_attributes(attrs: Any) -> dict[str, Any]:
    if not isinstance(attrs, list):
        raise TempoTerminalError("attributes must be a list")
    return {
        str(item["key"]): _unwrap_otlp_value(item.get("value", {}))
        for item in attrs
        if isinstance(item, dict) and "key" in item
    }


def _unwrap_otlp_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    return None


def _decode_otlp_bytes(value: str, *, expected_len: int) -> bytes:
    if not value:
        raise ValueError("empty OTLP byte field")
    try:
        decoded = bytes.fromhex(value)
        if len(decoded) >= expected_len:
            # Some Tempo search payloads include a suffix on otherwise-hex
            # trace IDs. The span fetch endpoint still uses the canonical
            # first 16 bytes.
            return decoded[:expected_len]
    except ValueError:
        pass
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("OTLP byte field is neither hex nor base64") from exc
    if len(decoded) != expected_len:
        raise ValueError(
            f"OTLP byte field decoded to {len(decoded)} bytes; "
            f"expected {expected_len}"
        )
    return decoded
