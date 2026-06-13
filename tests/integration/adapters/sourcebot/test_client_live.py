"""Plan 8 Task 6 — SourcebotClient live integration test.

Runs only with `pytest --run-live-sourcebot` against an environment-
configured Sourcebot instance. Required env vars:

  CODEORACLE_SOURCEBOT__BASE_URL       — e.g. http://localhost:3000
  CODEORACLE_SOURCEBOT__API_KEY        — bearer token for the instance
  CODEORACLE_SOURCEBOT__DEFAULT_REPO   — repo identifier in Sourcebot
                                  (the instance MUST have this repo indexed)
  CODEORACLE_SOURCEBOT_LIVE_REF        — git ref to fetch from (commit / branch
  / tag) CODEORACLE_SOURCEBOT_LIVE_PATH       — known file path inside the repo
  CODEORACLE_SOURCEBOT_LIVE_LINES      — line range as "<start>-<end>",
  inclusive CODEORACLE_SOURCEBOT_LIVE_EXPECTED   — exact expected slice content
  (newline-joined)

The expected-content assertion is intentionally byte-exact: if the wire
format ever drifts (e.g., Sourcebot rewrites line endings, prepends BOM,
returns base64-encoded source), the assertion fires loudly with a diff
the operator can read.
"""

import os

import httpx
import pytest
from pydantic import SecretStr

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse

pytestmark = pytest.mark.live_sourcebot


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"live-sourcebot test requires env {name!r}")
    return val


@pytest.fixture
async def live_client():
    # async-generator fixture — no return annotation (see Task 5's `client`).
    cfg = SourcebotConfig(
        base_url=_require_env("CODEORACLE_SOURCEBOT__BASE_URL"),
        api_key=SecretStr(_require_env("CODEORACLE_SOURCEBOT__API_KEY")),
        timeout_seconds=10.0,
        default_repo=_require_env("CODEORACLE_SOURCEBOT__DEFAULT_REPO"),
    )
    c = SourcebotClient(cfg)
    try:
        yield c
    finally:
        await c.aclose()


async def test_fetch_span_against_live_sourcebot(
    live_client: SourcebotClient,
) -> None:
    ref = _require_env("CODEORACLE_SOURCEBOT_LIVE_REF")
    path = _require_env("CODEORACLE_SOURCEBOT_LIVE_PATH")
    line_spec = _require_env("CODEORACLE_SOURCEBOT_LIVE_LINES")
    try:
        start_str, end_str = line_spec.split("-", 1)
        line_range = (int(start_str), int(end_str))
    except ValueError as e:
        pytest.skip(
            "CODEORACLE_SOURCEBOT_LIVE_LINES must be 'start-end', "
            f"got {line_spec!r}: {e}"
        )

    expected = _require_env("CODEORACLE_SOURCEBOT_LIVE_EXPECTED")

    actual = await live_client.fetch_span(
        path=path, line_range=line_range, ref=ref
    )
    assert actual == expected, (
        f"\nExpected:\n{expected!r}\n"
        f"\nActual:\n{actual!r}\n"
        "\nDiff is a Sourcebot wire-format drift — investigate "
        "/api/source response shape."
    )

    # Explicit contract validation on the live wire shape — the live test
    # is no longer the only line of defense for D-8.4. The mocked tests
    # (Task 5) and this live test now share `SourcebotSourceResponse` as
    # their single source of truth; any drift fails BOTH simultaneously.
    base_url = _require_env("CODEORACLE_SOURCEBOT__BASE_URL").rstrip("/")
    api_key = _require_env("CODEORACLE_SOURCEBOT__API_KEY")
    repo = _require_env("CODEORACLE_SOURCEBOT__DEFAULT_REPO")
    async with httpx.AsyncClient(timeout=10.0) as raw:
        response = await raw.get(
            f"{base_url}/api/source",
            params={"repo": repo, "path": path, "ref": ref},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
    response.raise_for_status()
    # If Sourcebot's wire shape drifts (extra field, missing source, wrong
    # type), ValidationError fires here with a structured diff. The exact
    # same model gates the adapter (Task 4) and the mocked tests (Task 5),
    # so live and mocked paths cannot disagree on what "valid" means.
    SourcebotSourceResponse.model_validate(response.json())
