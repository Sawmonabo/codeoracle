"""Extractor execution-mode literal — Plan 6 frame extension.

`per_file` — orchestrator enqueues one message per (file, extractor). The
worker
processes one file per message and writes one `SnapshotFileOutcome` row. This
is
the natural fit for tools that operate on a single file at a time: Joern (Plan
5), OpenGrep (this plan), embed (this plan).

`project` — orchestrator enqueues ONE message per (snapshot, extractor)
carrying
the full file list. The worker runs the tool once on the project and then
writes
N `SnapshotFileOutcome` rows (one per file in the list, regardless of whether
the tool covered each file individually). This fits tools that are designed to
operate at project scope: scip-python (this plan).

Plan 7 (continuous traces ingest) is expected to extend this literal to add a
third mode, `continuous`, that has neither snapshot-keyed nor per-file
semantics. When Plan 7 lands, both the literal here AND the `_INFO_BY_KIND`
map in `orchestrator.py` need a coordinated update; the literal is the
single source of truth for which modes the orchestrator understands.
"""

from typing import Literal

ExtractorMode = Literal["per_file", "project"]
