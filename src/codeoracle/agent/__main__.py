"""Agent process entrypoint: ``python -m codeoracle.agent``."""

import uvicorn

from codeoracle.agent.app import create_app
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
        host=settings.agent.host,
        port=settings.agent.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
