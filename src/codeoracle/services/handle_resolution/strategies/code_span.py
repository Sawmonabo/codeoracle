"""CodeSpanResolver — resolves file://path:start-end handles via Sourcebot.

Mapped to HandleType.CODE_SPAN by composition root. The DocParagraphResolver
(Task 10) uses the same locator format + same SourcebotClient; both classes
exist as distinct types so trust-matrix scoring (Plan 9) can differentiate
code vs doc citations.
"""

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.core.exceptions import ResolverError, SourcebotError
from codeoracle.models.evidence import EvidenceHandle
from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)


class CodeSpanResolver(HandleResolverStrategy):
    """Resolves CODE_SPAN handles by fetching the cited lines from Sourcebot.

    The resolver does NOT normalize returned content — the Verifier
    (Plan 9) normalizes at comparison time (NFC + LF + per-line rstrip)
    on BOTH the excerpt and the resolved content. Normalizing here
    would erase information the Verifier wants to see.

    Snapshot SHA normalization: the resolver strips the CodeOracle
    ``+wt<12hex>`` dirty-tree suffix via `head_sha_only` (Task 8a) before
    passing the ref to Sourcebot — Sourcebot indexes only true git refs
    (closes G21 / M-8.1).
    """

    def __init__(self, sourcebot: SourcebotClient) -> None:
        self._sourcebot = sourcebot

    async def resolve(self, handle: EvidenceHandle) -> str:
        path, line_range = parse_file_locator(handle.locator)
        try:
            return await self._sourcebot.fetch_span(
                path=path,
                line_range=line_range,
                ref=head_sha_only(handle.snapshot_sha),
            )
        except SourcebotError as e:
            raise ResolverError(
                f"CodeSpanResolver could not fetch {handle.locator!r} at "
                f"snapshot {handle.snapshot_sha!r}: {e}"
            ) from e
