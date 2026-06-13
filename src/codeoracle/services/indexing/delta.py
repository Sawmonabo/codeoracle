"""DeltaComputer — given a base SHA, return the set of files needing
re-extraction.

Pure stdlib for the git side (subprocess on `git`) + GraphRepository
for the dependent-discovery cypher. Lives in services/ (not adapters/)
because it composes the git CLI + the graph DB into a domain decision
("what does the next snapshot need to cover?").

Three responsibilities (Plan 5 scope):
  1. Compute the current snapshot's SHA — head SHA, or
     '<head>+wt<sha256>[12]' if the working tree is dirty.
  2. Compute the set of changed files: git diff <base>..HEAD plus
     untracked + modified working-tree files.
  3. Discover dependents via spec §8.4's two-pattern Cypher — files
     that DEFINE a caller of (CALLS), or that IMPORT, a changed file.
"""

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codeoracle.db.repositories.graph import GraphRepository


@dataclass(frozen=True)
class Changeset:
    """Output of DeltaComputer.compute().

    Attributes: new_snapshot_sha: the SHA the next snapshot should bear —
            either the clean HEAD SHA or '<head>+wt<digest>'.
        head_sha: the raw `git rev-parse HEAD` value.
        is_dirty: whether the working tree had uncommitted changes.
        changed_files: union of (git diff <base>..HEAD) + working-tree
            modifications + untracked files. Repo-root-relative paths.
        dependent_files: files that DEFINE a caller of (CALLS), or that
            IMPORT, a changed file — per spec §8.4's two-pattern Cypher
            over the graph at the BASE snapshot (or [] when base is None
            or absent in the graph).
    """

    new_snapshot_sha: str
    head_sha: str
    is_dirty: bool
    changed_files: list[str]
    dependent_files: list[str]

    @property
    def all_files(self) -> list[str]:
        """Union of changed + dependents, deduplicated, sorted."""
        return sorted(set(self.changed_files) | set(self.dependent_files))


class DeltaComputer:
    """Compute the next snapshot's changeset.

    Construct with the repo root (defaults to CWD) and a GraphRepository
    for dependent discovery. The repo root must be a git working tree —
    `git rev-parse HEAD` runs inside it.
    """

    def __init__(
        self,
        *,
        graph_repo: GraphRepository,
        repo_root: Path | None = None,
    ) -> None:
        self._graph = graph_repo
        self._root = (repo_root or Path.cwd()).resolve()

    async def compute(self, *, base_sha: str | None) -> Changeset:
        """Compute the changeset since `base_sha`.

        If `base_sha` is None, the caller is doing a full reindex — but
        Plan 5's slice-acceptance flow still computes a "delta" to
        produce a consistent shape; with base=None, we treat every
        tracked file as changed and skip dependent discovery.
        """
        head_sha = self._git("rev-parse", "HEAD")
        wt_status = self._git("status", "--porcelain")
        is_dirty = bool(wt_status.strip())

        if base_sha is None:
            # Full reindex — every tracked file
            changed = self._git("ls-files").splitlines()
            return Changeset(
                new_snapshot_sha=self._snapshot_sha(head_sha, is_dirty),
                head_sha=head_sha,
                is_dirty=is_dirty,
                changed_files=sorted(changed),
                dependent_files=[],
            )

        # Diff committed changes
        committed = self._git(
            "diff", "--name-only", f"{base_sha}..HEAD"
        ).splitlines()
        # Modified + untracked working-tree files (porcelain format)
        wt_files = self._parse_porcelain_files(wt_status)

        changed_set = set(committed) | wt_files
        changed_sorted = sorted(changed_set)

        # Dependent discovery — query the BASE snapshot's graph for files
        # that depend on any changed file, per spec §8.4's two-pattern
        # contract: (1) files defining a caller of a symbol defined in a
        # changed file (Symbol-level CALLS graph, post-linking-pass), and
        # (2) files that import a changed file (File→File IMPORTS). One
        # query over the whole changed-file list — not a per-file loop.
        # The query already excludes changed files (WHERE NOT dep.file_path IN
        # $changed_files), so no separate dedup against changed_set. File nodes
        # key their path under `file_path` — the parser's per-file partition
        # key,
        # shared with replace_file_subgraph's DELETE; `path` is not a property,
        # so the accessor must be `file_path` (the `AS path` below is only the
        # output-column alias the Python reads as row["path"]).
        dependent_rows = await self._graph.execute_cypher(
            "MATCH (changed:File {snapshot_sha:$base})-[:DEFINES]->"
            "(target:Symbol)<-[:CALLS]-(caller_def:Symbol)<-[:DEFINES]-"
            "(dep:File) "
            "WHERE changed.file_path IN $changed_files "
            "AND NOT dep.file_path IN $changed_files "
            "RETURN DISTINCT dep.file_path AS path "
            "UNION "
            "MATCH (dep:File)-[:IMPORTS]->(changed:File {snapshot_sha:$base}) "
            "WHERE changed.file_path IN $changed_files "
            "AND NOT dep.file_path IN $changed_files "
            "RETURN DISTINCT dep.file_path AS path",
            {"base": base_sha, "changed_files": changed_sorted},
            column_spec="path agtype",
        )
        dependents = {str(row["path"]).strip('"') for row in dependent_rows}

        return Changeset(
            new_snapshot_sha=self._snapshot_sha(head_sha, is_dirty),
            head_sha=head_sha,
            is_dirty=is_dirty,
            changed_files=changed_sorted,
            dependent_files=sorted(dependents),
        )

    def _snapshot_sha(self, head_sha: str, is_dirty: bool) -> str:
        """Return '<head>' or '<head>+wt<digest>[12]' per spec §8.4."""
        if not is_dirty:
            return head_sha

        wt_digest = self._wt_digest()
        return f"{head_sha}+wt{wt_digest[:12]}"

    def _wt_digest(self) -> str:
        """sha256 of the concatenation of (path, content) for every
        git-tracked modified file. Untracked files are NOT hashed —
        only tracked changes contribute to identity (spec §8.4).
        """
        h = hashlib.sha256()
        # `git diff HEAD --name-only` lists modified tracked files
        modified = self._git("diff", "HEAD", "--name-only").splitlines()
        for rel_path in sorted(modified):
            abs_path = self._root / rel_path
            if not abs_path.is_file():
                continue  # deleted file
            h.update(rel_path.encode("utf-8"))
            h.update(b"\0")
            with abs_path.open("rb") as f:
                while chunk := f.read(8192):
                    h.update(chunk)
            h.update(b"\0")
        return h.hexdigest()

    def _git(self, *args: str) -> str:
        """Run `git <args>` in repo_root; return stdout (utf-8)."""
        result = subprocess.run(
            ["git", *args],
            cwd=self._root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    @staticmethod
    def _parse_porcelain_files(porcelain: str) -> set[str]:
        """Parse `git status --porcelain` output to a set of file paths.

        Porcelain format: `XY path` where X/Y are status codes. For
        renames: `XY old -> new`. Plan 5 treats both old and new as changed.
        """
        out: set[str] = set()
        for line in porcelain.splitlines():
            if not line.strip():
                continue
            # Skip 2 status chars + space
            entry = line[3:]
            if " -> " in entry:
                old, new = entry.split(" -> ", 1)
                out.add(old)
                out.add(new)
            else:
                out.add(entry)
        return out
