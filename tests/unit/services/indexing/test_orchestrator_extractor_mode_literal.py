"""ExtractorMode is a Literal type pinning two modes (per_file, project).

Plan 7 is expected to extend this literal to add "continuous" (for traces
ingest, which has neither snapshot-keyed nor per-file semantics). The literal
is intentionally NOT a class hierarchy or enum — keeping it as a string
literal lets it serialize trivially into Pydantic messages and into the
Snapshot.extractor_results dict.
"""

from typing import get_args

from codeoracle.services.indexing.extractor_mode import ExtractorMode
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator


def test_extractor_mode_literal_pins_two_modes() -> None:
    modes = set(get_args(ExtractorMode))
    assert modes == {"per_file", "project"}


def test_extractor_mode_used_in_orchestrator_info_map() -> None:
    info_map = IndexingOrchestrator._INFO_BY_KIND
    for kind, info in info_map.items():
        assert info.mode in get_args(ExtractorMode), (
            f"kind={kind} has invalid mode={info.mode}"
        )
