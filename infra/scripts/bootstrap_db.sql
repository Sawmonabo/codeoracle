-- Layer 1: one-shot DB bootstrap.
--
-- Creates the AGE + pgvector extensions, sets the per-database search_path,
-- and creates the 'codeoracle' AGE graph. Idempotent: re-running is a no-op.
--
-- Run as the Postgres SUPERUSER (the docker-compose 'codeoracle' user has
-- SUPERUSER on the dev DB; in prod this would be a one-time DBA action).
--
-- Plan 3 Task 9+ alembic migrations assume this script has already run.
-- Migrations NEVER call CREATE EXTENSION — that requires SUPERUSER which
-- the application role does not have in non-dev environments.

CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;

-- Per-database default search_path. `public` is FIRST so unqualified
-- CREATE TABLE in alembic migrations lands in `public`, not in ag_catalog.
-- ag_catalog stays in the path so AGE functions (cypher, create_graph, etc.)
-- still resolve unqualified. Reordering this line would cause every
-- application table to land in ag_catalog — a load-bearing decision.
ALTER DATABASE :"db_name" SET search_path = "$user", public, ag_catalog;

-- LOAD age on the bootstrap connection so create_graph resolves.
LOAD 'age';

-- Idempotently create the 'codeoracle' graph.
DO $$ BEGIN
  PERFORM create_graph('codeoracle');
EXCEPTION WHEN duplicate_schema THEN
  NULL;
END $$;

-- ── Graph property indexes (NFR D3-1) ──────────────────────────────────────
-- AGE auto-indexes only each vertex's `id` and each edge's start/end id — never
-- user properties. The agent's hot path filters vertices BY property on every
-- query: the symbol-existence check `MATCH (s:Symbol {fqn, snapshot_sha})`
-- (Plan 10 `_EXISTS_CYPHER`) and the callers entry
-- `MATCH (:File {snapshot_sha})-[...]->(:Symbol {fqn})`. With no property index
-- each is a full Seq Scan over the whole label table — once per user query,
-- over millions of multi-snapshot vertices. Plan 5 owns graph *writes* but
-- treats this graph schema as Plan 3's ("Inherits from Plan 3"), so the indexes
-- live here, beside create_graph, and exist BEFORE the first write (no
-- unindexed-scan window).
--
-- Pre-create the two foundational vertex labels so the indexes can be built now
-- rather than waiting for the graph-writer to create each label lazily on its
-- first MERGE. create_vlabel has no IF-NOT-EXISTS form and raises on a
-- duplicate; guard on the label's backing table (`<graph>."<Label>"`, an
-- ordinary table) via the standard `to_regclass` instead of swallowing the
-- error broadly. In dev/CI the single superuser `codeoracle` role owns these; a
-- split-role prod deployment grants the app role DML on them in this same DBA
-- bootstrap step.
DO $$ BEGIN
  IF to_regclass('codeoracle."Symbol"') IS NULL THEN PERFORM create_vlabel('codeoracle', 'Symbol'); END IF;
  IF to_regclass('codeoracle."File"')   IS NULL THEN PERFORM create_vlabel('codeoracle', 'File');   END IF;
END $$;

-- Btree indexes on the exact agtype property-access expression AGE emits for a
-- `{key: $val}` vertex-map match. The expression MUST match the planner's
-- predicate or the index is never chosen — an integration test EXPLAINs the
-- existence check under `enable_seqscan = off` and asserts it plans on
-- symbol_fqn_idx. `create_property_index('codeoracle','Symbol','fqn')` builds the
-- same index via AGE's helper; the explicit DDL is kept so the indexed
-- expression is self-documenting.
CREATE INDEX IF NOT EXISTS symbol_fqn_idx
  ON codeoracle."Symbol" USING btree (ag_catalog.agtype_access_operator(properties, '"fqn"'::agtype));
CREATE INDEX IF NOT EXISTS symbol_snapshot_sha_idx
  ON codeoracle."Symbol" USING btree (ag_catalog.agtype_access_operator(properties, '"snapshot_sha"'::agtype));
CREATE INDEX IF NOT EXISTS file_snapshot_sha_idx
  ON codeoracle."File" USING btree (ag_catalog.agtype_access_operator(properties, '"snapshot_sha"'::agtype));
