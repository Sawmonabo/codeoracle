"""StubResolver — explicit "not supported in slice scope" for TRACE_SPAN
and DB_RECORD handles.

Composition root registers two instances:
    HandleType.TRACE_SPAN: StubResolver(HandleType.TRACE_SPAN)
    HandleType.DB_RECORD:  StubResolver(HandleType.DB_RECORD)

Each instance carries its `handle_type` so the error message names what
the LLM asked for. Plan 13's E2E slice asserts that the intent classifier
never emits these types — i.e., the stubs should never fire in practice.
If one does, it's a regression worth investigating.
"""

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
)


class StubResolver(HandleResolverStrategy):
    """Always-fail resolver. Registered for HandleTypes that the slice
    intent classifier should never emit but that exist in the enum so
    Phase 2+ extensions can add real strategies without touching the
    HandleType enum or the dispatcher."""

    def __init__(self, handle_type: HandleType) -> None:
        self._handle_type = handle_type

    async def resolve(self, handle: EvidenceHandle) -> str:
        raise UnsupportedHandleTypeError(
            f"HandleType.{self._handle_type.name} "
            f"({self._handle_type.value!r}) is not supported in slice "
            "scope — the agent should not emit this handle type. "
            f"locator={handle.locator!r}"
        )
