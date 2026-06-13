"""Plan 8 Task 5 — SourcebotClient unit tests (respx-mocked HTTP).

These tests pin the contracts in the plan-level Invariants and the
wire-format gotchas listed in Task 4. Each test below maps to one row
in the Task 5 test matrix.
"""

import httpx
import pytest
import respx
from pydantic import SecretStr

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse
from codeoracle.core.exceptions import (
    SourcebotTerminalError,
    SourcebotTransientError,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

SOURCEBOT_BASE = "http://sourcebot.test:3000"
DEFAULT_REPO = "github.com/Sawmonabo/codeoracle"
DEFAULT_REF = "abc1234"
DEFAULT_PATH = "src/codeoracle/services/verifier/service.py"
DEFAULT_API_KEY = "test-api-key-123"


def _make_config() -> SourcebotConfig:
    """Build SourcebotConfig with test values — bypasses env lookup."""
    return SourcebotConfig(
        base_url=SOURCEBOT_BASE,
        api_key=SecretStr(DEFAULT_API_KEY),
        timeout_seconds=2.0,
        default_repo=DEFAULT_REPO,
    )


@pytest.fixture
async def client():
    """Async-yield SourcebotClient — pytest-asyncio adapts plain @fixture
    on an async generator under `asyncio_mode = "auto"` (Plan 3 / Plan 7
    convention). No return annotation: this is an async *generator* (it
    yields, not returns), so `-> SourcebotClient` would be wrong and
    ty-strict flags it — matches Plan 7's TempoClient fixture."""
    c = SourcebotClient(_make_config())
    try:
        yield c
    finally:
        await c.aclose()


def make_source_payload(source: str) -> dict[str, object]:
    """Construct a valid GET /api/source response body via the contract model.

    Every mocked fixture derives from `SourcebotSourceResponse` so the
    mocked path validates exactly what the live path does (closes D-8.4).
    """
    return SourcebotSourceResponse(source=source).model_dump()


def _ten_line_file() -> str:
    """A 10-line file used by line-slice tests. No trailing newline so the
    splitlines semantics are unambiguous: `splitlines()` returns 10 items."""
    return "\n".join(f"line {i}" for i in range(1, 11))


# -----------------------------------------------------------------------------
# Happy-path slicing
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_returns_inclusive_one_indexed_slice(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(_ten_line_file())
    )
    result = await client.fetch_span(
        path=DEFAULT_PATH, line_range=(3, 5), ref=DEFAULT_REF
    )
    # Lines 3, 4, 5 inclusive — 3 lines.
    assert result == "line 3\nline 4\nline 5"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_clamps_line_end_past_eof(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(_ten_line_file())
    )
    # File has 10 lines; ask for 8-20.
    result = await client.fetch_span(
        path=DEFAULT_PATH, line_range=(8, 20), ref=DEFAULT_REF
    )
    assert result == "line 8\nline 9\nline 10"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_returns_empty_when_line_start_past_eof(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(_ten_line_file())
    )
    result = await client.fetch_span(
        path=DEFAULT_PATH, line_range=(50, 60), ref=DEFAULT_REF
    )
    assert result == ""


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_handles_empty_source_field(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload("")
    )
    result = await client.fetch_span(
        path=DEFAULT_PATH, line_range=(1, 10), ref=DEFAULT_REF
    )
    assert result == ""


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_preserves_internal_newlines_no_trailing_newline(
    client: SourcebotClient,
) -> None:
    """
    splitlines / \"\\n\".join semantics: internal \\n preserved, trailing \\n
    on the file body is NOT re-added by the slicer. Invariant 3."""
    body = "a\nb\nc\n"  # trailing \n present in source
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(body)
    )
    result = await client.fetch_span(
        path=DEFAULT_PATH, line_range=(1, 3), ref=DEFAULT_REF
    )
    assert result == "a\nb\nc"  # NO trailing \n


# -----------------------------------------------------------------------------
# Wire format — headers + query params
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_sends_bearer_auth_header(
    client: SourcebotClient,
) -> None:
    route = respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(_ten_line_file())
    )
    await client.fetch_span(
        path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
    )
    assert route.called
    request = route.calls[0].request
    assert request.headers.get("authorization") == f"Bearer {DEFAULT_API_KEY}"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_sends_repo_path_ref_query_params(
    client: SourcebotClient,
) -> None:
    route = respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(_ten_line_file())
    )
    await client.fetch_span(
        path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
    )
    assert route.called
    params = dict(route.calls[0].request.url.params)
    assert params["repo"] == DEFAULT_REPO
    assert params["path"] == DEFAULT_PATH
    assert params["ref"] == DEFAULT_REF


# -----------------------------------------------------------------------------
# Input validation — caller bugs
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_span_rejects_line_start_below_one(
    client: SourcebotClient,
) -> None:
    with pytest.raises(ValueError, match="line_start must be >= 1"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(0, 10), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
async def test_fetch_span_rejects_line_end_below_line_start(
    client: SourcebotClient,
) -> None:
    with pytest.raises(ValueError, match=r"line_end .* must be >= line_start"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(10, 5), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
async def test_fetch_span_rejects_empty_path(client: SourcebotClient) -> None:
    with pytest.raises(ValueError, match="path must be non-empty"):
        await client.fetch_span(path="", line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
async def test_fetch_span_rejects_empty_ref(client: SourcebotClient) -> None:
    with pytest.raises(ValueError, match="ref must be non-empty"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref="")


# -----------------------------------------------------------------------------
# HTTP error classification
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_404_raises_terminal(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=404, text="path not found"
    )
    with pytest.raises(SourcebotTerminalError, match="404"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_401_raises_terminal(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=401, text="unauthorized"
    )
    with pytest.raises(SourcebotTerminalError, match="401"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_500_raises_transient(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=500, text="boom"
    )
    with pytest.raises(SourcebotTransientError, match="500"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_429_raises_transient(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=429, text="too many requests"
    )
    with pytest.raises(SourcebotTransientError, match="429"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_timeout_raises_transient(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    with pytest.raises(SourcebotTransientError, match="timeout"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_connect_error_raises_transient(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(SourcebotTransientError, match="connect error"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_non_json_body_raises_terminal(
    client: SourcebotClient,
) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=200, text="<html>nope</html>"
    )
    with pytest.raises(SourcebotTerminalError, match="non-JSON"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_missing_source_field_raises_terminal(
    client: SourcebotClient,
) -> None:
    # Valid JSON but missing the `source` key — Sourcebot would never do this,
    # but if a deploy proxy strips fields we want a typed failure. Contract
    # validation surfaces the drift; extra=forbid additionally rejects the
    # `language`/`path` fields shown here.
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=200, json={"language": "Python", "path": DEFAULT_PATH}
    )
    with pytest.raises(SourcebotTerminalError, match="contract validation"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


# -----------------------------------------------------------------------------
# Contract drift — D-8.4
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_rejects_extra_unexpected_field_in_response(
    client: SourcebotClient,
) -> None:
    """SourcebotSourceResponse uses extra=forbid — an unexpected upstream
    field surfaces as SourcebotTerminalError. The mocked-vs-live contract
    is the same model, so this drift fails identically in both paths
    (closes D-8.4)."""
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=200, json={"source": "x", "extra": 1}
    )
    with pytest.raises(SourcebotTerminalError, match="contract validation"):
        await client.fetch_span(
            path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF
        )


# -----------------------------------------------------------------------------
# Layer purity — adapter passes ref through unchanged (G21/M-8.1)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_passes_wt_suffixed_ref_through_unchanged(
    client: SourcebotClient,
) -> None:
    """The adapter is layer-pure: it forwards the `ref` query param exactly
    as received. Stripping the CodeOracle ``+wt<12hex>`` dirty-tree
    suffix is the resolver's job (Task 8a / Task 9 / Task 10), NOT the
    adapter's. This test pins that the adapter does NOT silently rewrite
    the ref — if a caller passes the dirty-tree form, it goes out on the
    wire that way."""
    dirty_ref = "abc123+wt0123456789ab"
    route = respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(_ten_line_file())
    )
    await client.fetch_span(
        path=DEFAULT_PATH, line_range=(1, 1), ref=dirty_ref
    )
    assert route.called
    params = dict(route.calls[0].request.url.params)
    assert params["ref"] == dirty_ref


# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    """Calling aclose twice MUST NOT raise — composition root may invoke it
    from both an atexit hook and an explicit shutdown path."""
    c = SourcebotClient(_make_config())
    await c.aclose()
    await c.aclose()  # second call: must be a no-op
