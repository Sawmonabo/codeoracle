"""API process entrypoint — `python -m codeoracle.api`.

Plan 1A's Dockerfile entrypoint shim invokes this module for the API
variant (`api -> Plan 11` per Plan 1A's variant table). At slice scope
the API runs as a single uvicorn process (workers=1) bound to the
host/port from `settings.api`.

Startup order is invariant — every Plan 1A-rooted process module uses
this exact sequence:
  1. configure_logging(settings) — install structlog renderer
  2. init_telemetry()             — install OTel TracerProvider + exporter
  3. create_app(settings)         — build FastAPI app + lifespan
  4. uvicorn.run(app, host, port)

Amended 2026-05-26: reindex is async (invariant 11 amended); the route
returns in <50ms after enqueueing, so uvicorn's default
`timeout_keep_alive` (5s) is sufficient. The pre-amendment sizing
(`reindex_timeout_seconds + 30s headroom`) is no longer needed.
"""

import uvicorn

from codeoracle.api.main import create_app
from codeoracle.core.config import get_settings
from codeoracle.core.logging import configure_logging
from codeoracle.core.telemetry import init_telemetry


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    init_telemetry()
    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.api.host,
        port=settings.api.port,
        # log_config=None disables uvicorn's own logging setup so
        # configure_logging() is the single source of truth for log
        # formatting (Plan 3's structlog pipeline).
        log_config=None,
    )


if __name__ == "__main__":
    main()
