"""Compatibility shim for the split API dependency package.

New code should import from ``codeoracle.api.dependencies``.
"""

from codeoracle.api.dependencies import *  # noqa: F403
from codeoracle.api.dependencies import __all__ as _dependency_all

__all__ = list(_dependency_all)
