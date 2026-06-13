"""
Platform version surfacing — single source of truth read by API health
endpoints.
"""

__version__ = "0.1.0"  # x-release-please-version


def get_version() -> str:
    return __version__
