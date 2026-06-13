# `codeoracle.adapters.traces` — Tempo HTTP adapter

Plan 7 boundary between `httpx` + Tempo HTTP API and the rest of the
platform. Everything else in `codeoracle/` consumes `Span` / `TraceSummary`
domain objects, never raw OTLP dicts.

## Endpoints

| Endpoint | What it does | When we use it |
| --- | --- | --- |
| `GET /api/search?q={}&start=&end=&limit=` | TraceQL match-all in time window; returns trace summaries | `TempoClient.search_traces_in_window` — per-tick enumeration |
| `GET /api/v2/traces/{hex}` | Full OTLP payload for one trace | `TempoClient.get_trace_by_id` — per-trace fetch |
| `GET /ready` | Liveness probe | Healthcheck only (no code path) |
| `GET /status/version` | Tempo build info | Future startup-time version assertion |

## Wire-format gotchas (load-bearing)

1. **Nanos are strings in OTLP-JSON.** `startTimeUnixNano="1700000000000000000"` — preserve precision, JS can't hold 64-bit ints. Cast `int(...)` at adapter boundary.
2. **IDs are hex OR base64 — Tempo varies by build.** `traceId` is 32 hex chars (16 bytes) on most builds OR 24 base64-standard chars on Tempo 2.6+ with the OTLP-JSON marshaller; `spanId` mirrors the encoding at half the length; `parentSpanId` same, OR empty string `""` for root spans (map `""` → `None`). Decode via `_decode_otlp_bytes(encoded, expected_len=16/8)` which tries hex first and falls back to base64 — never call `bytes.fromhex` directly at the adapter boundary.
3. **Attributes are tagged unions.** `attributes: [{key, value: {stringValue|intValue|boolValue|doubleValue|arrayValue|kvlistValue|bytesValue}}]`. Recursive unwrap via `_unwrap_otlp_value`.
4. **`kind` is an integer.** `0=unspecified, 1=internal, 2=server, 3=client, 4=producer, 5=consumer`. Map via `_OTLP_KIND_MAP`.
5. **`status.code` is also an integer.** `0=unset, 1=ok, 2=error`. Map via `_OTLP_STATUS_CODE_MAP`.
6. **`scope` is per-scopeSpans, not per-span.** Copy `scope.name` + `scope.version` onto every Span in the flatten step.
7. **`service.name` is on `resource.attributes`, not on individual spans.** Pull from resource, propagate to every Span.

## Error classification

| Wire error | Mapped to | Notes |
| --- | --- | --- |
| 5xx | `TempoTransientError` | Backoff + retry by caller |
| 429 | `TempoTransientError` | Rate-limit — caller should also slow down |
| 4xx (other) | `TempoTerminalError` | Don't retry — fix config/query |
| connect / DNS error | `TempoTransientError` | |
| Read/write timeout | `TempoTransientError` | |
| Non-JSON response body | `TempoTerminalError` | Likely wrong path or proxy intercept |
| Missing/extra fields in JSON | `TempoTerminalError` | Schema drift — surface, don't paper over |

## What this adapter does NOT do

- **Push to Tempo** — Tempo ingest is via the OTel Collector, not this adapter. We read-only here.
- **Multi-tenant auth** — `X-Scope-OrgID` is injected by deployment config (env var → header) if the deployment needs it; not hardcoded.
- **Retries** — `ContinuousConsumer` owns the retry loop. The adapter raises typed errors and lets the caller decide.
