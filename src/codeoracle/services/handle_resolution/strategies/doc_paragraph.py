"""DocParagraphResolver — resolves file://path:start-end handles via Sourcebot.

Mapped to HandleType.DOC_PARAGRAPH. Mechanically identical to
CodeSpanResolver (Task 9); the distinction matters at TrustMatrix
scoring time (docs score 3, code scores 10 per architecture §16).

Future extension seam: when a docs-only vector index lands (architecture
§6 roadmap), DocParagraphResolver routes there; CodeSpanResolver stays
on Sourcebot. Separate classes today => zero refactoring later.
"""

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.core.exceptions import ResolverError, SourcebotError
from codeoracle.models.evidence import EvidenceHandle
from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)


class DocParagraphResolver(HandleResolverStrategy):
    """Resolves DOC_PARAGRAPH handles via Sourcebot (same I/O path as
    CodeSpanResolver). The type distinction drives downstream trust
    scoring, not the resolution mechanism.

    Snapshot SHA normalization: same as CodeSpanResolver — the
    ``+wt<12hex>`` dirty-tree suffix is stripped via `head_sha_only`
    (Task 8a) before the ref reaches Sourcebot (closes G21 / M-8.1).
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
                f"DocParagraphResolver could not fetch {handle.locator!r} at "
                f"snapshot {handle.snapshot_sha!r}: {e}"
            ) from e
