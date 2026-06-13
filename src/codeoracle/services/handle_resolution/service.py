"""HandleResolverService — dispatches an EvidenceHandle to the right strategy.

Construction: svc = HandleResolverService(strategies={
        HandleType.CODE_SPAN:     CodeSpanResolver(sourcebot),
        HandleType.DOC_PARAGRAPH: DocParagraphResolver(sourcebot),
        HandleType.GRAPH_QUERY:   GraphQueryResolver(graph_repo),
        HandleType.TRACE_SPAN:    StubResolver(HandleType.TRACE_SPAN),
        HandleType.DB_RECORD:     StubResolver(HandleType.DB_RECORD), })

The composition root (Plan 11 api/deps.py) is the only place this dict
is built. Tests construct their own subsets.
"""

from typing import Protocol

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType


class HandleResolverStrategyProtocol(Protocol):
    async def resolve(self, handle: EvidenceHandle) -> str: ...


class HandleResolverService:
    """Stateless dispatcher: handle.type -> registered strategy.

    Stateless per Invariant 5 (no caching, no batching). Every call hits
    the strategy's underlying I/O. Concurrency is the Verifier's job —
    it calls `asyncio.gather(*self._resolve_one(c) for c in citations)`.
    """

    def __init__(
        self,
        *,
        strategies: dict[HandleType, HandleResolverStrategyProtocol],
    ) -> None:
        self._strategies = strategies

    async def resolve(self, handle: EvidenceHandle) -> str:
        """Route `handle` to its strategy and return the resolved content.

        Raises:
            UnsupportedHandleTypeError: no strategy registered for handle.type.
            ResolverError (or subclass): strategy could not fetch.
            Any other exception raised by the strategy: propagated as-is.
        """
        strategy = self._strategies.get(handle.type)
        if strategy is None:
            raise UnsupportedHandleTypeError(
                "no resolver strategy registered for "
                f"HandleType.{handle.type.name} "
                f"(value={handle.type.value!r}); composition root MUST "
                "wire all "
                f"HandleType members — see Plan 8 Invariant 8"
            )
        return await strategy.resolve(handle)

    def registered_handle_types(self) -> set[HandleType]:
        """Return the set of HandleType values with a registered strategy.

        The composition-root coverage test (Plan 8 Task 13) calls this to
        assert every HandleType is wired — without reaching into the
        private `_strategies` dict, which a rename would silently break.
        """
        return set(self._strategies)
