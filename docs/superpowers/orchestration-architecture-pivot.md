# Orchestration Architecture Pivot — Working Notes

**Date:** 2026-05-22 (session `c1940097-3836-4699-a39b-dd8222741388`)
**Status:** PRE-COMMIT — draft amendments not yet applied to spec
**Type:** Working-tree artifact (DO NOT COMMIT — same status as `plan-audit-findings.md`)
**Purpose:** Preserve fresh architectural-decision context across potential session compaction. Read this doc to recover state, then apply the §1.4 + §10 spec amendments described below.

> **CORRECTION 2026-05-22 (post-re-ground):** This doc was originally drafted before re-reading the spec. Spec re-read revealed §1.2 ("What the platform is") and §10 ("References") are ALREADY TAKEN. Corrected insertion points: **new boundary subsection is §1.4** (after §1.3 "Why scaffold now") — not §1.2. **New target-architecture top-level section is §10**, with current §10 (References) renumbering to §11 (§10.1→§11.1, §10.2→§11.2, §10.3→§11.3). Inside the draft §10 text below, §10.5's cross-reference reads "(re-affirms §1.2)" — when applied to the spec it must say **"(re-affirms §1.4)"**. Advisor (called 2026-05-22 after re-ground) also flagged three polish items folded into the actual spec edits: (1) strengthened §10.1 opening to foreground slice-vs-target distinction; (2) step-budget calibration sentence added to §10.3; (3) two-LLM-pattern trade-off acknowledgement added to §10.3.

---

## Why this doc exists

During the spec-amendments commit-approval pause (Task 1 of 14), the user challenged the slice's platform-driven retrieval architecture with five concrete engineer-style questions (Q1-Q5 below). The exchange revealed that the slice's single-intent design cascades to UNKNOWN for compound queries, AND that pure LLM-driven retrieval breaks the evidence-binding invariant. The architectural answer is **Orchestrator + PrimitiveRegistry + Intent Classifier**, layered ON TOP of the slice (not replacing it).

The user chose **option (a)** of three offered: draft the §1.2 (synthesis boundary) and §10 (target architecture) amendments now and fold them into the current in-flight spec-amendment pass before requesting commit approval. The user explicitly asked for this doc to ensure context-compaction survival before the spec work proceeds.

---

## The 5 stress-test questions (verbatim)

1. *"How is information pulled into 'X' record from 'Y'?"* — **data-lineage trace** (multi-step)
2. *"What is the process flow of 'X'?"* — **entry-to-terminal-state trace** (multi-step)
3. *"How does our logic handle 'X' situation?"* — **behavioral / concept search** (semantic, not symbol-bound)
4. *"I need to pull 'X' data from the datastore with a database query, can you help me write the query to grab it?"* — **synthesis (OUT OF SCOPE for CodeOracle)**
5. *"When I do 'X' on the front end, 'Y' happens. Is that intended behavior or an issue?"* — **actual-vs-intended comparison** (multi-source)

### What the questions revealed

| Insight | Consequence |
|---|---|
| Most real engineer questions are compound (3-7 primitive fetches composed via reasoning). | Single-intent enum cascades to UNKNOWN on phrasing variance. |
| Some questions (Q4) are synthesis, not understanding. | CodeOracle needs an explicit out-of-scope boundary. |
| Pure platform-driven retrieval is brittle to NL phrasing. | Need an LLM intent classifier at the front. |
| Pure LLM-driven retrieval breaks evidence-binding (skipped evidence, tool hallucination, nondeterminism, unbounded cost). | LLM must never directly fetch bytes; primitives stay platform-deterministic. |
| The "specific agent whose job is to pick" the user asked about is the **orchestrator** — it picks **intents** (not raw tools), each of which the platform expands deterministically. | Need a second LangGraph agent above the slice. |

---

## The architectural fix — 3 additions + 2 boundaries

### Addition 1: PrimitiveRegistry — replace fixed enums with extensible registry

Today the spec has `Query.type: Literal["explain_symbol"]` and a closed-set `HandleType` enum. Both freeze the platform at one primitive. The fix:

- Replace `Literal` with a typed **PrimitiveRegistry**: each primitive declares `(name, input_schema, output_handle_types, resolver_fn)`.
- LLM-facing intent dispatch and platform-facing handle production both use the registry as their boundary.
- New primitives are added by writing one resolver class + registering it — no agent-graph changes required.

**Minimum primitive set covering Q1-Q5 + general codebase questions:**

| # | Primitive | Used by | Produces handle types | Status |
|---|---|---|---|---|
| 1 | `explain_symbol(fqn)` | Q1, Q2, Q5, base | `CODE_SPAN`, `GRAPH_QUERY` | **exists** (slice) |
| 2 | `find_callers(fqn)` | Q1, Q5 | `GRAPH_QUERY`, `CODE_SPAN` | **partial** (slice `graph_query` node) |
| 3 | `trace_forward / trace_upstream(fqn, depth)` | Q1, Q2 | `GRAPH_QUERY` | new (graph walk variants) |
| 4 | `find_writers_of / find_readers_of(target)` | Q1, Q4-evidence | `GRAPH_QUERY`, `CODE_SPAN` | new (needs `:DB_WRITES`, `:DB_READS` edges added to §3.7) |
| 5 | `find_entrypoints_for(concept)` | Q2, Q5 | `CODE_SPAN`, `GRAPH_QUERY` | new (embedding + route registry) |
| 6 | `find_handlers_for(endpoint)` | Q5 | `CODE_SPAN`, `GRAPH_QUERY` | new |
| 7 | `find_traces_for(target)` | Q2, Q5 | `TRACE_SPAN` | new (Tempo query layer; builds on Plan 7 ingestion) |
| 8 | `find_specs_for(concept)` | Q2, Q3, Q5 | `DOC_PARAGRAPH` | **partial** (handle type exists in spec; no doc ingester) |
| 9 | `find_tests_for(target)` | Q3, Q5 | `CODE_SPAN` | new |
| 10 | `find_db_schema(collection)` | Q4-evidence | `DB_RECORD` | new |
| 11 | `find_db_query_patterns(target)` | Q3, Q4-evidence | `CODE_SPAN` | new |
| 12 | `find_frontend_action(name)` | Q5 | `CODE_SPAN` | new (frontend extractor) |
| 13 | `semantic_search(concept)` | Q3, fallback | `CODE_SPAN` | **exists** (slice `embed_query` node) |

**Contract:** every primitive returns **typed handles**, never raw bytes. The Verifier round-trips every handle regardless of which primitive emitted it. Evidence-binding survives expansion to N primitives.

### Addition 2: Orchestrator agent — LLM loop driver above the slice

A second LangGraph agent that sits above the slice agent. The slice agent (`explain_symbol`) becomes **one primitive** that the orchestrator can dispatch.

```
┌──────────────── Orchestrator Agent (LLM loop) ────────────────┐
│                                                                 │
│  classify_intent  →  initial primitives_queue                   │
│                                                                 │
│  loop (step budget = 8):                                        │
│    pop next primitive                                           │
│    dispatch → PrimitiveRegistry → typed bundle                  │
│    LLM examines accumulated bundle                              │
│    decides: enqueue more / stop / give up with diagnostic       │
│                                                                 │
│  compose answer (existing compose pattern)                      │
│  verify (existing verifier — every cite round-trips)            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

- Bounded by step budget (default 8 dispatches) to prevent runaway loops.
- LLM may extend the queue mid-loop based on what came back — that's the "agent that picks" capability.
- LLM never authors evidence, only requests primitive dispatches.

### Addition 3: Intent classifier — LLM front door

Small LLM call at the top: free-text NL → initial `OrchestrationPlan` (typed list of primitive invocations). Absorbs phrasing variance.

**If the LLM cannot map the question to ANY primitive in the registry:** return honest `UNKNOWN` with a diagnostic:

> "I can answer questions about *<list of categories>*. Your question asks about *<unclassified phrase>*, which isn't a supported intent. Try rephrasing or check the supported-question catalog at *<URL>*."

This is better than fabricating an answer — the UNKNOWN-cliff your concern surfaced moves to the *vocabulary boundary*, where it's honest and actionable instead of silent.

### Boundary A: Synthesis is explicitly out of scope

Q4-class questions ("write a query / refactor this / generate a function") are NOT CodeOracle's job. Document in spec §1.2:

> CodeOracle produces verifiable evidence about **code that exists**. Synthesis (authoring new code) is delegated to dev-assist tooling (Claude Code, Cursor, Copilot). CodeOracle exposes its primitive registry via MCP so dev-assist tools can ground their generation in CodeOracle's evidence: schema + existing query patterns + indexes + caller context.

This is a **moat, not a hole.** The evidence-binding invariant is what makes CodeOracle trustworthy, and it's structurally incompatible with synthesis (cannot cite bytes that don't exist yet). Acknowledging the boundary makes CodeOracle **complementary** to dev-assist agents, not competitive.

### Boundary B: LLM roles are named, bounded, and never authorial

| Role | Allowed | Forbidden |
|---|---|---|
| Intent classifier | Pick primitives from registry; reject ambiguous queries with diagnostic | Invent primitives; bypass registry; fabricate fallback |
| Orchestrator | Pick **next** primitive given accumulated bundle | Author bytes; skip dispatched primitive; exceed step budget |
| Composer (existing) | Select evidence by index, write prose, cite by index | Author byte content; emit uncited claims; modify excerpts |

What no LLM ever does: fetch bytes; author citations; retry an unresolved handle; exceed step budget.

---

## Impact on the 13-plan series — honest delta

| Plan | Today | Action |
|---|---|---|
| 1–9 | Foundation (skeleton, DB, LLM facade, workers, extractors, traces, resolvers, verifier) | **UNCHANGED.** They build the substrate. Doesn't matter how many primitives sit on top. |
| 10 | Slice agent (one intent: `explain_symbol`) | **UNCHANGED.** Slice becomes "one primitive in the registry." Plan 10's `intent_node` hardcoded plan promotes to a registry entry as a 1-2 task refactor later. |
| 11 (API — unwritten) | HTTP entry | **minor shape change** — accept both structured `{type, ...}` input AND `{free_text_query}` orchestrator-routed input. |
| 12 (MCP — unwritten) | MCP tools | **CHANGES MEANINGFULLY.** MCP exposes the **PrimitiveRegistry** as one MCP tool per primitive, so external agents (Claude Code, Cursor) can dispatch directly without going through CodeOracle's orchestrator. This is the synthesis-tool integration story (Boundary A). |
| 13 (E2E slice — unwritten) | Integration test | **UNCHANGED.** Proves single-primitive path works end-to-end. |
| **NEW Plan 14** | **Orchestrator agent + PrimitiveRegistry promotion** | New. Wraps slice with LLM loop driver. Promotes Plan 10's hardcoded plan to a registry entry. |
| **NEW Plan 15** | **Doc + test extractors (`find_specs_for`, `find_tests_for`)** | New. Lowest-cost primitives to add; closes Q3 + Q2-spec + Q5-spec gaps. |
| **NEW Plan 16** | **DB-schema + frontend extractors (`find_db_schema`, `find_frontend_action`, `find_db_query_patterns`)** | New. Closes Q4-evidence + Q5-frontend gaps. |
| **NEW Plan 17** | **Trace primitives (`find_traces_for`)** | New. Builds on Plan 7's ingestion to add Tempo query primitives. |
| **NEW Plan 18** | **Data-flow primitives (`find_writers_of`, `find_readers_of`, `trace_upstream`)** | New. Extends Joern parser contract (§3.7) with `:DB_WRITES`, `:DB_READS` edges. |

## Decision sequencing (recommended)

1. **Slice ships first (Plans 1-13)** — proves evidence-binding end-to-end with one primitive.
2. **Plan 14 (orchestrator + registry promotion)** — wraps the slice with the LLM loop; small initial primitive set.
3. **Plans 15-18** — extractor breadth grows the primitive catalog incrementally.

Rationale: the slice's discipline (deterministic primitives, evidence-binding) IS the foundation. Plans 14-18 build ON the slice; they don't replace it. The committed work (Plans 1-13) stays the same.

---

## Draft spec amendments — exact text to apply

### Draft §1.2 — Scope boundary: understanding vs synthesis

Insert as a new subsection inside **§1 Context and motivation**, immediately after §1.1 (the two-roles-working-directory subsection). Verbatim text:

```markdown
### 1.2 What CodeOracle is — and explicitly is not

CodeOracle produces **verifiable evidence about code that exists**. Every claim CodeOracle emits ships with citations that round-trip through the platform's verifier — the LLM may explain, but only the platform can prove. If the platform cannot prove, the verdict is `UNKNOWN`.

**In scope:** retrieval, comprehension, trace-bound analysis, and audit-of-record over an indexed snapshot. Questions of the form *"how does X work / who calls X / what does spec Y say about behavior Z / what runtime trace evidence exists for endpoint W"* — anything where the answer is grounded in bytes that already exist somewhere in the indexed codebase, the trace store, or the doc corpus.

**Out of scope: synthesis.** CodeOracle does not author new code, generate queries, propose refactors, or write tests. Those tasks belong to dev-assist tooling (Claude Code, Cursor, Copilot) and require capabilities (code generation, edit ergonomics, conversational state) CodeOracle deliberately does not build. The evidence-binding invariant that makes CodeOracle trustworthy is structurally incompatible with synthesis — you cannot cite bytes that do not exist yet.

**Integration path with synthesis tooling.** CodeOracle exposes its primitive registry (see §10) via MCP. A user authoring a datastore query against an indexed application snapshot uses Claude Code for the synthesis itself and dispatches CodeOracle's `find_db_schema` / `find_db_query_patterns` / `find_indexes` primitives via MCP to ground that generation in real evidence from the codebase. The two tools are complementary, not competitive: CodeOracle supplies provable context, the synthesis tool authors the change.

This boundary is load-bearing. Trying to grow CodeOracle into a general-purpose dev assistant would dissolve the property that justifies its existence.
```

### Draft §10 — Target architecture: primitive catalog + orchestrator

Insert as a new top-level section **§10**, after current §9 ("Decisions deliberately deferred") and before §11 (whatever the current final section is — check before applying). Verbatim text:

```markdown
## 10. Target architecture: primitive catalog, orchestrator, and intent classifier

The slice (this spec's primary scope, Plans 1-13) ships **one** primitive end-to-end: `explain_symbol`. The slice is the foundation pattern — a single deterministic intent dispatch, platform-fetched evidence, LLM composition, byte-binding verification — and the rest of the platform grows by adding more primitives that follow the same shape, plus a loop-driver agent above them.

This section describes the target architecture so downstream plans (14+) inherit a consistent design. The slice does NOT implement §10 today; it implements §10's foundation pattern correctly so the rest can be built incrementally.

### 10.1 Slice scope vs target scope

| Layer | Slice (Plans 1-13) | Target (Plans 14-18+) |
|---|---|---|
| Intent | `Query.type: Literal["explain_symbol"]` — typed at API boundary, no LLM classifier | Free-text NL → LLM intent classifier → typed `OrchestrationPlan` |
| Retrieval | One agent, six nodes, one plan (`[CODE_SPAN, GRAPH_QUERY]`) | N primitives, each with its own resolver; orchestrator LLM loop dispatches them in sequence |
| Composition | Compose node selects evidence by index, writes prose | Same — composition pattern unchanged |
| Verification | Verifier round-trips every cite | Same — verifier unchanged; works on any handle type |

The slice's discipline (deterministic primitives, platform-owned bytes, LLM-selects-not-authors) IS the target architecture's foundation. The target just adds an orchestrator above and more primitives alongside.

### 10.2 PrimitiveRegistry

Replace fixed `Literal` intent types with an extensible **PrimitiveRegistry**. Each primitive declares:

```python
class Primitive(Protocol):
    name: str  # e.g., "find_callers"
    input_schema: type[BaseModel]  # Pydantic input
    output_handle_types: tuple[HandleType, ...]  # which handles it can produce
    async def resolve(self, input: BaseModel, deps: AgentDependencies) -> EvidenceBundle: ...
```

A `PrimitiveRegistry` keeps a dict of primitives by name. Both the LLM intent classifier and the orchestrator loop dispatch through the registry — the registry IS the platform/LLM boundary.

Minimum primitive set covering common codebase-question shapes is enumerated in §10.6 (planning roadmap). The slice's `explain_symbol` is the first registry entry; Plans 15-18 add the rest.

### 10.3 Orchestrator agent — LLM loop driver

A second LangGraph agent (separate from the slice's single-intent agent, which becomes one primitive within the registry):

```
Free-text question OR structured intent
    ↓
[intent_classifier node]  LLM → initial primitive queue
    ↓
[orchestrator loop node]  (bounded by step budget, default 8)
    pop next primitive from queue
    dispatch via PrimitiveRegistry → typed EvidenceBundle
    LLM examines accumulated bundle, decides:
        - enqueue more primitives, OR
        - stop (sufficient evidence), OR
        - give up with diagnostic
    ↓
[compose node]            LLM authors prose, selects evidence by index
    ↓
[verify node]             Verifier round-trips every cite (handle-type agnostic)
    ↓
QueryResult
```

Bounds:
- Step budget — default 8 dispatches per query; configurable per intent class.
- Token budget — composition stays single-shot (no retry loops in compose; that's a post-slice hardening item).
- Registry-only dispatch — orchestrator cannot invoke anything that isn't a registered primitive. No raw tool calls.

### 10.4 Intent classifier — front door

A small LLM call at the top of the orchestrator agent translates free-text NL into an initial `OrchestrationPlan`. Schema:

```python
class OrchestrationPlan(BaseModel):
    primitives: list[PrimitiveInvocation]  # initial queue
    confidence: float                       # classifier's confidence
    unsupported_reason: str | None          # populated when no primitives match
```

If the classifier cannot map the question to any registered primitive, it returns `unsupported_reason` and the orchestrator emits an honest `UNKNOWN` with a diagnostic listing supported intent categories. The vocabulary boundary is *named*, not silent.

API and MCP callers may bypass the classifier by supplying a `{type, ...}` structured query — Plan 11 / Plan 12 design those paths.

### 10.5 LLM role boundaries (re-affirms §1.2)

The LLM is invoked at three named places, each with bounded authority. NONE of them fetches bytes or authors citations:

| Role | Allowed | Forbidden |
|---|---|---|
| Intent classifier (§10.4) | Pick primitives from registry; reject ambiguous queries with diagnostic | Invent primitives; bypass registry |
| Orchestrator (§10.3) | Pick **next** primitive given accumulated bundle | Author bytes; skip dispatched primitive; exceed step budget |
| Composer (existing slice node) | Select evidence by index, write prose, cite by index | Author byte content; emit uncited claims; modify excerpts |

What no LLM ever does: fetch bytes; author citations; retry an unresolved handle; exceed the step budget.

### 10.6 Planning roadmap — Plans 14-18

The slice (Plans 1-13) ships one primitive (`explain_symbol`) end-to-end. Subsequent plans grow the primitive catalog and add the orchestration shell:

| Plan | Scope | Adds |
|---|---|---|
| 14 | Orchestrator agent + PrimitiveRegistry promotion | LLM loop driver; promotes Plan 10's hardcoded plan to a registry entry |
| 15 | Doc + test extractors | `find_specs_for(concept)` and `find_tests_for(target)` primitives |
| 16 | DB-schema + frontend extractors | `find_db_schema`, `find_db_query_patterns`, `find_frontend_action` primitives |
| 17 | Trace primitives | `find_traces_for` primitive (builds on Plan 7's OTel ingestion) |
| 18 | Data-flow primitives | `find_writers_of`, `find_readers_of`, `trace_upstream` primitives (extends §3.7 with `:DB_WRITES`, `:DB_READS` edges) |

Plans 14-18 are explicitly out of slice scope. They are listed here so that Plans 11-13 (API, MCP, E2E) design their interfaces to accommodate the future orchestration layer without rework.
```

---

## Workflow recovery — where we are

| Stage | Status |
|---|---|
| Original 6 spec amendments (changelog, §3.2, §3.5, §3.7, §7.5, §8.4, §9.3 #3) | **APPLIED to spec file, NOT COMMITTED.** |
| Advisor pass on those amendments | **DONE.** One blocking finding (§3.7 ↔ §8.4 schema reconciliation) + two polish items + one precision fix applied. |
| §1.2 + §10 amendments described in this doc | **NOT YET APPLIED.** Next step. |
| Advisor pass on combined diff (original + §1.2 + §10) | Pending. |
| User commit approval | Pending. |
| Tasks 2-14 | Pending (next per the task list). |

## Standing constraints (re-affirm)

- Commit the SPEC FILE ONLY: `git add docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`.
- NEVER stage generated orchestration artifacts.
- NEVER stage this very doc (`docs/superpowers/orchestration-architecture-pivot.md`) — it's a working-tree artifact, same status as `plan-audit-findings.md`.
- Add files by explicit name only — never `git add -A` or `git add .`.
- Active-doc path hygiene: do not encode external target working-tree paths or audit snapshot directory names in platform docs; keep external corpus and snapshot scope explicit for each task instead of inheriting historical audit paths.

## Next steps (after this doc lands)

1. **Re-ground** — re-read this doc and check the spec's current state to confirm insertion points for §1.2 (after §1.1) and §10 (after §9, before whatever §11 currently is).
2. **Advisor confirm** — invoke `advisor()` BEFORE applying the §1.2 + §10 edits. The advisor sees the full transcript; ask it to verify the architecture holds, the boundary statements don't oversell, and the §10 draft is internally consistent + consistent with the spec's existing sections (§3 services, §4 service interactions, §9 deferred decisions).
3. **Apply** — Edit the spec to insert §1.2 and §10. Update the spec's `## Changelog` block with two new rows.
4. **Surface diff to user** — request commit approval per the standing "won't commit until you ask" rule. Show diff if requested.
5. **Commit** — single commit covering the full spec amendment pass (original 6 + §1.2 + §10).
6. **Mark Task 1 complete** and proceed to Task 2 (write new infra plan via writing-plans skill).
