"""Settings dependency for API routes."""

from typing import Annotated

from fastapi import Depends, Request

from codeoracle.api.dependencies._state import get_api_resources
from codeoracle.core.config import Settings


def get_settings_dep(request: Request) -> Settings:
    """Return the process-wide settings object."""

    return get_api_resources(request).settings


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
