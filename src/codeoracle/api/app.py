"""Compatibility shim for the API application factory.

New code should import ``create_app`` from ``codeoracle.api.main``.
"""

from codeoracle.api.main import create_app

__all__ = ["create_app"]
