"""Private helpers for API dependency modules."""

from fastapi import Request

from codeoracle.api.resources import ApiResources


def require_app_state(request: Request, slot: str) -> object:
    """Return ``request.app.state.<slot>`` or raise a clear runtime error."""

    value = getattr(request.app.state, slot, None)
    if value is None:
        raise RuntimeError(
            f"request.app.state.{slot} is missing; "
            "the API lifespan did not initialize it."
        )
    return value


def get_api_resources(request: Request) -> ApiResources:
    """Return the typed API resource container from application state."""

    value = require_app_state(request, "resources")
    if not isinstance(value, ApiResources):
        raise RuntimeError(
            "request.app.state.resources has the wrong type; "
            "expected codeoracle.api.resources.ApiResources."
        )
    return value
