"""Plan 8 Task 8 — HandleResolverService dispatch tests.

The service has zero domain logic of its own — it's pure dispatch. Tests pin:
  1. Constructor accepts dict[HandleType, HandleResolverStrategy].
  2. resolve() routes to the strategy whose key matches handle.type.
  3. Unmapped handle.type raises UnsupportedHandleTypeError.
  4. The strategy receives the original handle (not a copy / re-wrap).
  5. Exceptions from the strategy propagate unchanged (no wrapping).
  6. registered_handle_types() exposes the dispatch dict's keys.
"""

import pytest

from codeoracle.core.exceptions import (
    ResolverError,
    UnsupportedHandleTypeError,
)
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.service import HandleResolverService
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
)


class _RecordingStrategy(HandleResolverStrategy):
    """Test double — records every handle it was asked to resolve."""

    def __init__(
        self, return_value: str = "ok", raise_exc: Exception | None = None
    ) -> None:
        self.calls: list[EvidenceHandle] = []
        self._return_value = return_value
        self._raise_exc = raise_exc

    async def resolve(self, handle: EvidenceHandle) -> str:
        self.calls.append(handle)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._return_value


def _handle(
    handle_type: HandleType, locator: str = "file://x.py:1-1"
) -> EvidenceHandle:
    return EvidenceHandle(
        type=handle_type, locator=locator, snapshot_sha="abc1234"
    )


@pytest.mark.asyncio
async def test_resolve_dispatches_to_strategy_keyed_by_handle_type() -> None:
    code_span = _RecordingStrategy(return_value="code content")
    doc_para = _RecordingStrategy(return_value="doc content")
    svc = HandleResolverService(
        strategies={
            HandleType.CODE_SPAN: code_span,
            HandleType.DOC_PARAGRAPH: doc_para,
        }
    )

    result_code = await svc.resolve(_handle(HandleType.CODE_SPAN))
    assert result_code == "code content"
    assert len(code_span.calls) == 1
    assert len(doc_para.calls) == 0

    result_doc = await svc.resolve(_handle(HandleType.DOC_PARAGRAPH))
    assert result_doc == "doc content"
    assert len(code_span.calls) == 1
    assert len(doc_para.calls) == 1


@pytest.mark.asyncio
async def test_resolve_passes_handle_through_unchanged() -> None:
    """The strategy MUST receive the original handle instance — no copy,
    no re-wrap. Mutating downstream is impossible (EvidenceHandle is
    frozen — see Plan 3 Task 4), but identity equality lets tests pin
    'no wrapper class in between'."""
    strategy = _RecordingStrategy()
    svc = HandleResolverService(strategies={HandleType.CODE_SPAN: strategy})
    original = _handle(
        HandleType.CODE_SPAN, locator="file://exact/path.py:7-9"
    )

    await svc.resolve(original)
    assert strategy.calls[0] is original  # identity, not just equality


@pytest.mark.asyncio
async def test_resolve_raises_unsupported_for_unmapped_handle_type() -> None:
    """If the dispatch dict has no entry, UnsupportedHandleTypeError fires.
    This is the safety net for new HandleType values added in the enum
    but not yet wired into composition root."""
    svc = HandleResolverService(
        strategies={
            HandleType.CODE_SPAN: _RecordingStrategy(),
        }
    )
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await svc.resolve(_handle(HandleType.GRAPH_QUERY))
    assert "graph_query" in str(exc.value)


@pytest.mark.asyncio
async def test_resolve_propagates_strategy_exceptions_unchanged() -> None:
    """If a strategy raises, the service MUST NOT wrap or swallow.
    The Verifier wants the real cause (so logs preserve it)."""
    original_exc = ResolverError("sourcebot returned 404")
    strategy = _RecordingStrategy(raise_exc=original_exc)
    svc = HandleResolverService(strategies={HandleType.CODE_SPAN: strategy})

    with pytest.raises(ResolverError) as exc:
        await svc.resolve(_handle(HandleType.CODE_SPAN))
    assert exc.value is original_exc


@pytest.mark.asyncio
async def test_resolve_propagates_non_resolver_exceptions_unchanged() -> None:
    """Strategy bugs (e.g., bare KeyError, TypeError) propagate too —
    the service doesn't try to "helpfully" mask them as ResolverError.
    Plan 9 VerifierService catches only ResolverError; anything else
    bubbles to the API layer's error handler (Plan 11)."""
    strategy = _RecordingStrategy(raise_exc=RuntimeError("bug"))
    svc = HandleResolverService(strategies={HandleType.CODE_SPAN: strategy})
    with pytest.raises(RuntimeError, match="bug"):
        await svc.resolve(_handle(HandleType.CODE_SPAN))


def test_constructor_accepts_empty_strategies_dict() -> None:
    """Edge case: an empty dict is a legal construction (every resolve()
    will raise UnsupportedHandleTypeError, which is the correct behavior
    for an unconfigured service)."""
    svc = HandleResolverService(strategies={})
    assert svc is not None


def test_registered_handle_types_reflects_constructor_dict() -> None:
    """`registered_handle_types()` exposes the dispatch dict's keys as a
    set — the public accessor the composition-root coverage test
    (Task 13) uses instead of reaching into private `_strategies`."""
    svc = HandleResolverService(
        strategies={
            HandleType.CODE_SPAN: _RecordingStrategy(),
            HandleType.DOC_PARAGRAPH: _RecordingStrategy(),
        }
    )
    assert svc.registered_handle_types() == {
        HandleType.CODE_SPAN,
        HandleType.DOC_PARAGRAPH,
    }
    assert (
        HandleResolverService(strategies={}).registered_handle_types() == set()
    )
