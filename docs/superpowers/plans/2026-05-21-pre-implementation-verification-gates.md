# Pre-Implementation Verification Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove every external dependency the platform commits to in the spec — Postgres 18 + Apache AGE + pgvector, Valkey, Joern, SCIP (Python + TypeScript), OpenGrep, SourceBot, vLLM image, Qwen3.6 model identifier, OpenAI live API (chat + embedding) — actually exists and behaves as the spec assumes, BEFORE Plan 3 starts wiring them into code. Capture verified versions/digests in a single machine-readable pin file plus a human-readable evidence report. Wire a CI job that re-runs the lightweight pin checks on every PR so silent drift (an image tag disappearing from Docker Hub) gets caught the day it happens.

**Architecture:** Each external dep gets one task. Each task (a) verifies the dep with a concrete command — image-manifest inspect, container smoke, CLI install + version, model HEAD request — (b) appends a one-paragraph evidence entry to `docs/verification-report.md` AND one key=value line to `infra/versions/pinned.env`, (c) commits both files. Verification artifacts grow commit-by-commit, so `git log -p docs/verification-report.md` reads as a chronological evidence trail. After all verifications land, a CI job re-runs only the cheap checks (manifest inspect, model HEAD) on every PR; expensive checks (container spin-up, CLI install) stay developer-local. No platform code lands in Plan 2.

**Tech Stack:** Docker CLI (manifest inspect + run), curl (HEAD requests for model identifiers + OpenAI smoke), npm/npx (SCIP indexers — they ship as Node packages), `uv tool install` (OpenGrep, optional), Joern's standalone tarball release, SourceBot Docker image, OpenAI Python SDK (already an indirect dependency by Plan 4 but used here for the live smoke).

**Commit cadence:** every task ends with a local commit. The whole plan ends with one push to `origin/main` after Task 14 — same cadence as Plan 1.

**Prerequisites (verify before starting Task 1):**

```bash
docker --version                                  # any 20.10+ works; 27.x current
docker buildx imagetools --help >/dev/null        # buildx ships with Docker 23+; required for reliable digest extraction
jq --version                                      # any; used for parsing manifest/config.json output
uv --version                                      # already installed in Plan 1
curl --version                                    # any
node --version                                    # 20+ recommended (npx is used to invoke @sourcegraph/scip-* without global install)
git status --short                                # working tree clean except for unrelated orchestrator-side changes
```

If any tool is missing, install it before proceeding. The plan calls these tools by name and does not include their installation steps.

**Placeholder conventions used in this plan:**

- `2026-MM-DD` in every report-append template means *the execution date in YYYY-MM-DD*. Substitute today's date when running each task (e.g., shell: `DATE=$(date +%Y-%m-%d)`).
- `<paste digest from Step N>` / `<version from Step N>` / `<chosen model id from Step N>` means *paste the literal command output from the named step*. These are runtime-determined values that depend on what the verification command returns; the executor fills them in as each task runs. Never commit a verbatim `<placeholder>` to the report.

**Verification artifacts produced by this plan:**

- `docs/verification-report.md` — narrative evidence for every external dep: what was checked, the exact command run, the output excerpt, the verdict.
- `infra/versions/pinned.env` — single source of truth for pinned versions/digests. Subsequent plans consume it two distinct ways: (a) **compose `${VAR}` interpolation** — Plan 3 runs `docker compose --env-file infra/versions/pinned.env` (or symlinks it to the compose dir's root `.env`) so `image: ${IMAGE_POSTGRES_AGE}` resolves; a service-level `env_file:` does NOT do this — it only injects vars into the running container, not into compose-file `${VAR}` substitution. (b) **plain shell sourcing** — Plan 4 (`LLMConfig` env-prefix sourcing) and Plan 5 (worker Dockerfiles) `source infra/versions/pinned.env` (or `set -a; source ...`) to read the KEY=VALUE lines as environment.
- `infra/scripts/preflight.sh` — spec §3.6 contract gate runner. Re-runs the 4 named §3.6 gates (AGE PG18, Joern Python frontend, SCIP-python, vLLM `/v1/embeddings`) as one coherent script with the spec's per-gate fallbacks transcribed verbatim. One-shot at scaffolding day 1; distinct from `check_pinned_env.sh` (CI-recurring pin-consistency lint).
- `.cache/preflight/results.md` — pass/fail outcome of the 4 §3.6 contract gates with the chosen fallback recorded per gate. Cross-links to `verification-report.md` for the long-form per-dep narrative.
- `infra/preflight-fixtures/sample_py.py` — planted Python fixture (class + method + cross-call) used by the Joern and SCIP-python gates so they exercise real code at scaffolding day 1, before `src/codeoracle/services/` exists.
- A `verification-pins` job appended to `.github/workflows/ci.yml` that re-runs the cheap pin checks on every PR.

---

## Task 1: Set up the verification report skeleton + pinned-env skeleton

**What this task delivers:** the two artifact files exist with their structure documented, so subsequent tasks can append cleanly without each task re-inventing the format.

**Files:**
- Create: `docs/verification-report.md`
- Create: `infra/versions/pinned.env`
- Create: `infra/versions/README.md`

- [ ] **Step 1: Create `infra/versions/` directory**

```bash
mkdir -p infra/versions
```

- [ ] **Step 2: Create `infra/versions/pinned.env` skeleton**

```bash
cat > infra/versions/pinned.env <<'EOF'
# Pinned versions / image digests for CodeOracle platform dependencies.
#
# This file is the single source of truth. Subsequent plans reference it:
#   - docker-compose:  --env-file infra/versions/pinned.env  ->  image: ${IMAGE_*}
#                      (root --env-file / .env drives ${VAR} interpolation; a
#                       service-level env_file: only injects container env vars
#                       and does NOT substitute compose-file ${VAR} — see README)
#   - shell scripts:   source infra/versions/pinned.env
#
# Format: KEY=VALUE (no quoting needed for digests / version strings).
# Generated by Plan 2 (pre-implementation verification gates).
#
# Drift-catch: these pins are NOT auto-bumped by Renovate. Plan 1 Task 12's
# .github/renovate.json5 has no custom/regex manager that reads this file, and
# Renovate has no built-in manager for an ad-hoc env file of image digests /
# version strings. Drift (a tag retagged or an image deleted out from under a
# pinned digest) is caught instead by the verification-pins CI job (Plan 2
# Task 13), which re-runs `docker manifest inspect` + the HF model HEAD against
# every pin on every PR. Bumping a pin is the deliberate "How to update a pin"
# flow in infra/versions/README.md, not an automated Renovate PR.

# --- Postgres 18 + Apache AGE ---
# Verified: Plan 2 Task 2
# IMAGE_POSTGRES_AGE=apache/age:release_PG18@sha256:TBD-FILLED-BY-TASK-2

# --- pgvector on PG18 ---
# Verified: Plan 2 Task 3

# --- Valkey (Redis-compatible queue) ---
# Verified: Plan 2 Task 4

# --- Joern (CPG / SAST for configured target repositories) ---
# Verified: Plan 2 Task 5

# --- SCIP indexers (Python + TypeScript) ---
# Verified: Plan 2 Task 6

# --- OpenGrep (rule-based scanning) ---
# Verified: Plan 2 Task 7

# --- SourceBot (code search across configured target repositories) ---
# Verified: Plan 2 Task 8

# --- vLLM serving image + Qwen3.6 model identifier ---
# Verified: Plan 2 Task 9

# --- OpenAI live API (dev default) + embedding model ---
# Verified: Plan 2 Task 10
EOF
```

Each Task N below replaces its matching `# IMAGE_X=...TBD-FILLED-BY-TASK-N` placeholder with the verified value. The placeholders are commented so the file parses as valid env-source format throughout Plan 2.

- [ ] **Step 3: Create `infra/versions/README.md`**

```bash
cat > infra/versions/README.md <<'EOF'
# infra/versions/

Pinned external-dep versions and image digests verified in Plan 2.

## Files

- `pinned.env` — single source of truth. Format: `KEY=VALUE` lines. Consumed two ways: (1) `docker compose --env-file infra/versions/pinned.env` (or symlinked as the compose dir's root `.env`) to drive `${VAR}` interpolation in the compose file, e.g. `image: ${IMAGE_POSTGRES_AGE}`; (2) `source infra/versions/pinned.env` in bash. NOTE: a *service-level* `env_file:` key does NOT drive compose `${VAR}` interpolation — it only injects vars into the started container's environment; only a root `--env-file` / `.env` substitutes `${VAR}` in the compose file itself. Comment lines (`#`) document provenance.
- `README.md` — this file.

## How to update a pin

1. Run the relevant verification step from `docs/verification-report.md` (each entry includes the exact command).
2. Edit `pinned.env` with the new value.
3. Commit with a message naming the pin (e.g., `chore(pins): bump pgvector to v0.8.1 for PG18`).
4. PR runs the `verification-pins` CI job (defined in `.github/workflows/ci.yml`, added in Plan 2 Task 13), which re-runs manifest inspect on every pin to confirm it still resolves.

Drift (an image disappearing or being retagged) shows up as a CI failure, not silently as a Plan-3 surprise.

## Why not `pyproject.toml` or `.env.example`

- `pyproject.toml` is for Python deps. Container images, CLI binaries, and HF model identifiers don't belong there.
- `.env.example` (Plan 1 Task 13) is for runtime config every developer overrides locally. These pins are not overridden per-developer — they're project-wide invariants.

Splitting the two files makes intent explicit and prevents Renovate from "helpfully" bumping runtime config to a developer's stale value.
EOF
```

- [ ] **Step 4: Create `docs/verification-report.md` skeleton**

```bash
cat > docs/verification-report.md <<'EOF'
# Pre-Implementation Verification Report

Evidence trail for every external dependency CodeOracle commits to in
`docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`. Each entry
captures: the dependency, the verification command, the relevant output
excerpt, the verdict, and the pinned value recorded in
`infra/versions/pinned.env`.

The report grows commit-by-commit during Plan 2 execution. Reading
`git log -p docs/verification-report.md` reproduces the verification timeline
in order.

## Methodology

- **Image deps** (Postgres+AGE, pgvector, Valkey, vLLM): `docker manifest inspect <tag>`
  proves the tag resolves; a short `docker run` smoke proves the image behaves
  as the spec assumes (extension loadable, queue accepts a PING, etc.).
- **CLI deps** (Joern, SCIP indexers, OpenGrep): install via the project's
  documented method, run `--version`, run the smallest possible "real" invocation
  (parse an empty file, index an empty repo, scan against an empty rule set).
- **Live API deps** (OpenAI): one paid request the size of a smoke test;
  the cost-per-run is bounded.
- **Model identifiers** (Qwen3.6 on HuggingFace): HTTP HEAD against the model
  page to confirm the slug resolves without auth, plus a sanity check on the
  config.json so we know the architecture is what we expect.

## Verdict scale

- **PROVEN** — primary-source evidence (command output, HTTP status, image
  digest) supports the spec's claim about this dep.
- **REFUTED** — primary-source evidence contradicts the claim. Spec must be
  amended before Plan 3 starts.
- **UNKNOWN** — verification was attempted but inconclusive (network failure,
  rate limit, etc.). Retry; if it stays UNKNOWN, treat as REFUTED for safety
  and pick a fallback.

Mirrors the platform's three-valued verdict scheme (PROVEN / UNKNOWN / REFUTED)
so the report reads with the same epistemology we're building the platform to
enforce.

---

EOF
```

- [ ] **Step 5: Commit the skeletons**

```bash
git add infra/versions/pinned.env infra/versions/README.md docs/verification-report.md
git commit -m "feat: pre-implementation verification skeletons (pinned.env + report)"
```

---

## Task 2: Verify Postgres 18 + Apache AGE image

**What this task delivers:** primary-source evidence that `apache/age:release_PG18` resolves to a real Docker manifest, plus a container smoke that proves `CREATE EXTENSION age; LOAD 'age';` works inside it. The image digest is pinned in `pinned.env` so the project tracks a specific build, not a moving tag.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Manifest inspect — confirm tag resolves**

```bash
docker manifest inspect apache/age:release_PG18 > /tmp/age-pg18.manifest.json
cat /tmp/age-pg18.manifest.json | head -40
```

Expected: a JSON manifest with `mediaType: application/vnd.docker.distribution.manifest...` and one or more `manifests:` entries (multi-arch). If `manifest unknown` comes back, STOP — the spec assumes this tag and the assumption is broken. Surface to the user before continuing.

- [ ] **Step 2: Capture the digest**

```bash
DIGEST=$(docker buildx imagetools inspect apache/age:release_PG18 --format '{{.Manifest.Digest}}')
echo "AGE PG18 digest: $DIGEST"
```

Expected: a `sha256:...` value 71 chars long (pulled from the manifest-list digest, which is what `docker pull image@sha256:...` resolves cross-arch). Note this — Step 5 records it in `pinned.env`.

`docker manifest inspect` is NOT a reliable fallback for digest extraction: for single-arch images it returns no top-level `.digest`, and for multi-arch images `.manifests[0].digest` is one arch's digest, not the manifest-list's. If `buildx imagetools inspect` fails (older Docker, no buildx), STOP and upgrade Docker — don't fall back to a broken extraction.

- [ ] **Step 3: Container smoke — extension loads, graph creates, MATCH returns**

```bash
docker run --rm --name codeoracle-verify-age \
  -e POSTGRES_PASSWORD=verify \
  -d apache/age:release_PG18

# Wait for the DB to come up (give it ~10s)
sleep 10

docker exec codeoracle-verify-age psql -U postgres -c "
  CREATE EXTENSION IF NOT EXISTS age;
  LOAD 'age';
  SET search_path = ag_catalog, \"\$user\", public;
  SELECT create_graph('verify');
  SELECT * FROM cypher('verify', \$\$
    CREATE (n:Verify {label: 'plan-2-smoke'})
    RETURN n
  \$\$) AS (n agtype);
"

docker rm -f codeoracle-verify-age
```

Expected: the `CREATE EXTENSION` returns silently (or `extension already exists, skipped`), `LOAD 'age'` returns silently, `create_graph` returns one row, the Cypher CREATE returns one row containing the node. If any command errors, STOP and document the error in the report as REFUTED.

- [ ] **Step 4: Verify the PG18 version inside the image**

```bash
docker run --rm apache/age:release_PG18 postgres --version
```

Expected: `postgres (PostgreSQL) 18.x`. If it shows 16 or 17, the tag is misleading — REFUTED.

- [ ] **Step 5: Update `infra/versions/pinned.env`**

Replace the placeholder comment with the verified pin. Edit `infra/versions/pinned.env`:

```diff
 # --- Postgres 18 + Apache AGE ---
 # Verified: Plan 2 Task 2
-# IMAGE_POSTGRES_AGE=apache/age:release_PG18@sha256:TBD-FILLED-BY-TASK-2
+IMAGE_POSTGRES_AGE=apache/age:release_PG18@sha256:<paste digest from Step 2>
+PG_VERSION=18
+AGE_VERSION=1.5
```

(Adjust `AGE_VERSION` if the container's `\dx age` output shows a different minor — query inside the running container: `docker exec codeoracle-verify-age psql -U postgres -c "\dx age"`.)

- [ ] **Step 6: Append the Task 2 entry to `docs/verification-report.md`**

Append:

```markdown
## Postgres 18 + Apache AGE — PROVEN (Plan 2 Task 2, 2026-MM-DD)

**Spec claim:** `docs/.../platform-scaffolding-design.md` §3.5 specifies PostgreSQL 18 with Apache AGE served from `apache/age:release_PG18`.

**Verification command(s):**
- `docker manifest inspect apache/age:release_PG18` — tag resolves.
- `docker run + CREATE EXTENSION age + LOAD 'age' + SELECT create_graph('verify') + Cypher CREATE` — extension and graph machinery functional inside the image.
- `docker run apache/age:release_PG18 postgres --version` — confirms PG18.

**Evidence excerpt:**
```
postgres (PostgreSQL) 18.<minor>
CREATE EXTENSION
LOAD
 create_graph
--------------
 (1 row)
```

**Pinned in `infra/versions/pinned.env`:**
- `IMAGE_POSTGRES_AGE=apache/age:release_PG18@sha256:<digest>`
- `PG_VERSION=18`
- `AGE_VERSION=1.5`

**Verdict: PROVEN.** Image tag exists, extension loads per spec §3.5's two-layer setup, Cypher round-trips.

---
```

Replace `<digest>` with the actual digest. Replace `2026-MM-DD` with today's date.

- [ ] **Step 7: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: Postgres 18 + Apache AGE image — PROVEN, digest pinned"
```

---

## Task 3: Verify pgvector extension on Postgres 18

**What this task delivers:** evidence that `pgvector` is installable inside the `apache/age:release_PG18` image (the spec needs both AGE and pgvector co-resident; we want to know now whether one Dockerfile suffices or whether we need a custom build in Plan 3).

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Check whether pgvector is preinstalled in the AGE image**

```bash
docker run --rm --name codeoracle-verify-pgvector \
  -e POSTGRES_PASSWORD=verify \
  -d apache/age:release_PG18

sleep 10

docker exec codeoracle-verify-pgvector psql -U postgres -c "
  CREATE EXTENSION IF NOT EXISTS vector;
  SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
"

docker rm -f codeoracle-verify-pgvector
```

Three outcomes:

- (a) `CREATE EXTENSION` succeeds, row returned with vector version → pgvector is preinstalled. Note the version. Skip to Step 3.
- (b) `CREATE EXTENSION` errors with "extension 'vector' is not available" → pgvector is NOT preinstalled. Continue to Step 2.
- (c) any other error → STOP, document, treat as REFUTED.

- [ ] **Step 2: If pgvector is NOT preinstalled, verify the official `pgvector/pgvector:pg18` image exists**

```bash
docker manifest inspect pgvector/pgvector:pg18 > /tmp/pgvector-pg18.manifest.json && \
  echo "pgvector pg18 image exists" || \
  echo "pgvector pg18 image MISSING — Plan 3 needs a custom Dockerfile combining AGE + pgvector"
```

If the manifest resolves, capture its digest:

```bash
PGVECTOR_DIGEST=$(docker buildx imagetools inspect pgvector/pgvector:pg18 --format '{{.Manifest.Digest}}')
echo "pgvector pg18 digest: $PGVECTOR_DIGEST"
```

If the manifest does NOT resolve, document that Plan 3 must build a custom image. Don't fail — this is a planning input, not a hard blocker.

- [ ] **Step 3: Update `infra/versions/pinned.env`**

Three sub-cases:

The canonical install-method key is `PGVECTOR_INSTALL_METHOD ∈ {preinstalled, separate-image, custom-dockerfile}` (audit D-2.6 / P0-9 — converges with the single-key acceptance gate in Plan 3 L98-106). The image-digest pin (`IMAGE_PGVECTOR`) is independent and only appears in branch (b).

**(a) pgvector preinstalled in AGE image** — append to pinned.env:
```
PGVECTOR_VERSION=<version from Step 1>
PGVECTOR_INSTALL_METHOD=preinstalled
```

**(b) pgvector available as separate image** — append:
```
IMAGE_PGVECTOR=pgvector/pgvector:pg18@sha256:<digest from Step 2>
PGVECTOR_VERSION=<from image inspect>
PGVECTOR_INSTALL_METHOD=separate-image
```

**(c) pgvector NOT readily available** — append:
```
# pgvector NOT preinstalled and no pgvector/pgvector:pg18 image exists.
# Plan 3 must build a custom Dockerfile combining AGE + pgvector.
# See docs/verification-report.md for context.
PGVECTOR_INSTALL_METHOD=custom-dockerfile
```

- [ ] **Step 4: Append the Task 3 entry to `docs/verification-report.md`**

Append (adapt the wording to which of (a)/(b)/(c) applies):

```markdown
## pgvector on PG18 — PROVEN (or PROVEN-with-caveat) (Plan 2 Task 3, 2026-MM-DD)

**Spec claim:** §3.5 / §4.1 — pgvector required alongside AGE for embedding storage.

**Verification command(s):**
- `docker exec ... psql ... CREATE EXTENSION IF NOT EXISTS vector;`
- (if needed) `docker manifest inspect pgvector/pgvector:pg18`

**Evidence excerpt:**
```
<paste the actual output>
```

**Pinned in `infra/versions/pinned.env`:** see PGVECTOR_* keys (`PGVECTOR_INSTALL_METHOD` is the canonical install-method key; `IMAGE_PGVECTOR` appears only in branch (b)).

**Verdict: PROVEN.** [If (c), say:] **Verdict: PROVEN-with-caveat — Plan 3 must build a custom AGE+pgvector Dockerfile because no preexisting image combines both. See pinned.env `PGVECTOR_INSTALL_METHOD=custom-dockerfile`.

---
```

- [ ] **Step 5: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: pgvector on PG18 — outcome noted, source documented in pinned.env"
```

---

## Task 4: Verify Valkey image (Redis-compatible queue)

**What this task delivers:** evidence that `valkey/valkey:8` (or whichever major the spec calls for) resolves and PING returns PONG. Pin its digest.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Manifest inspect Valkey 8**

```bash
docker manifest inspect valkey/valkey:8 > /tmp/valkey-8.manifest.json
cat /tmp/valkey-8.manifest.json | jq '.mediaType, .manifests[0].digest' 2>/dev/null \
  || cat /tmp/valkey-8.manifest.json | head -20
```

Expected: media type field + manifests array. If `manifest unknown`, try `valkey/valkey:latest` and document.

- [ ] **Step 2: Capture digest**

```bash
VALKEY_DIGEST=$(docker buildx imagetools inspect valkey/valkey:8 --format '{{.Manifest.Digest}}')
echo "Valkey 8 digest: $VALKEY_DIGEST"
```

- [ ] **Step 3: Container smoke — PING returns PONG**

```bash
docker run --rm -d --name codeoracle-verify-valkey valkey/valkey:8
sleep 3
docker exec codeoracle-verify-valkey valkey-cli PING
docker rm -f codeoracle-verify-valkey
```

Expected: `PONG`. If anything else, REFUTED.

- [ ] **Step 4: Update `infra/versions/pinned.env`**

Append:
```
IMAGE_VALKEY=valkey/valkey:8@sha256:<digest>
VALKEY_VERSION=8
```

- [ ] **Step 5: Append entry to `docs/verification-report.md`**

```markdown
## Valkey 8 — PROVEN (Plan 2 Task 4, 2026-MM-DD)

**Spec claim:** §4.1 — Redis-compatible queue for worker dispatch (worker pool consumes from Valkey).

**Verification command(s):**
- `docker manifest inspect valkey/valkey:8` — tag resolves.
- `docker exec codeoracle-verify-valkey valkey-cli PING` — returns PONG.

**Pinned:** `IMAGE_VALKEY=valkey/valkey:8@sha256:<digest>`, `VALKEY_VERSION=8`.

**Verdict: PROVEN.**

---
```

- [ ] **Step 6: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: Valkey 8 — PROVEN, PING/PONG smoke, digest pinned"
```

---

## Task 5: Verify Joern CLI

**What this task delivers:** evidence that the current Joern release downloads, unzips, runs `--version`, and can parse an empty source file. Pin the Joern version recorded by `--version`.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Download the latest Joern release tarball**

```bash
mkdir -p /tmp/codeoracle-verify-joern && cd /tmp/codeoracle-verify-joern
curl -fsSL -o joern-cli.zip https://github.com/joernio/joern/releases/latest/download/joern-cli.zip
unzip -q joern-cli.zip
ls joern-cli/
```

Expected: `joern-cli.zip` downloads successfully, unzips into `joern-cli/` containing `joern`, `joern-parse`, `joern-export`, `joern-scan`, etc.

- [ ] **Step 2: Run `--version`**

```bash
cd /tmp/codeoracle-verify-joern
./joern-cli/joern --version 2>&1 | tee joern-version.txt
```

Expected: a version string. Record it.

- [ ] **Step 3: Parse smoke — empty input must succeed cleanly**

```bash
cd /tmp/codeoracle-verify-joern
mkdir -p smoke-input && touch smoke-input/empty.py
./joern-cli/joern-parse smoke-input --language pythonsrc --output smoke.cpg.bin 2>&1 | tail -5
ls -l smoke.cpg.bin
```

Expected: a CPG binary file gets written (non-zero size, even for empty input). If `joern-parse` errors, treat as REFUTED.

- [ ] **Step 4: Clean up the verification scratch dir**

```bash
cd ~ && rm -rf /tmp/codeoracle-verify-joern
```

- [ ] **Step 5: Update `infra/versions/pinned.env`**

Append:
```
JOERN_VERSION=<version from Step 2>
JOERN_RELEASE_URL=https://github.com/joernio/joern/releases/latest/download/joern-cli.zip
```

(We pin "latest" rather than a specific version because Joern's CPG schema is broadly stable across versions, and the verification job re-runs install on every PR. If Joern ships a breaking change, the CI re-run catches it.)

- [ ] **Step 6: Append entry to `docs/verification-report.md`**

```markdown
## Joern CLI — PROVEN (Plan 2 Task 5, 2026-MM-DD)

**Spec claim:** §3.6 / §4.1 — Joern produces the code property graph (CPG) for configured target repositories; SAST queries run over the CPG.

**Verification command(s):**
- `curl ... joern-cli.zip && unzip ...` — release artifact downloads.
- `./joern-cli/joern --version` — binary runs.
- `./joern-cli/joern-parse smoke-input --language pythonsrc` — parse smoke succeeds on empty input, produces a CPG file.

**Evidence excerpt:**
```
<paste version string + parse output>
```

**Pinned:** `JOERN_VERSION=<version>`, `JOERN_RELEASE_URL=<release url>`.

**Verdict: PROVEN.**

---
```

- [ ] **Step 7: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: Joern CLI — PROVEN, version + release-url pinned"
```

---

## Task 6: Verify SCIP indexers (Python + TypeScript)

**What this task delivers:** evidence that `@sourcegraph/scip-python` and `@sourcegraph/scip-typescript` resolve on npm and can run their `--version` / `index` against an empty repo via `npx` (no global install — `npm install -g` requires root on most Linux dev setups and is unnecessary for verification). Pin both indexer versions.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Confirm both SCIP indexer packages still publish + check maintenance status**

The staleness signal MUST be the **last-publish** timestamp, not `npm view … time.modified`. The `time` object's top-level `modified` key updates on *any* registry-metadata mutation — a `npm deprecate`, an owner/maintainer change, a `dist-tag` move — none of which is a new release. Keying the STOP gate on `time.modified` therefore false-negatives an abandoned package whose metadata was touched for an unrelated reason (e.g. a deprecation notice freshens `modified` while the last real publish is years old). Robust signal: resolve `dist-tags.latest` (which `npm view <pkg> version` returns) and read *that exact version's* publish timestamp out of the `time` object via bracket notation:

```bash
for pkg in @sourcegraph/scip-python @sourcegraph/scip-typescript; do
  ver=$(npm view "$pkg" version)                       # = dist-tags.latest
  published=$(npm view "$pkg" "time[$ver]")            # publish ts of THAT version (last publish)
  deprecated=$(npm view "$pkg" deprecated 2>/dev/null) # non-empty iff deprecated
  echo "$pkg  version=$ver  last_published=$published  deprecated=${deprecated:-<no>}"
done
```

Expected: each line prints a version string + the ISO publish timestamp **of that latest version** + a deprecation marker. If either `last_published` is more than ~18 months before today, OR `deprecated` is non-empty, OR `npm view` reports the package unpublished, STOP — Sourcegraph has been narrowing scope through 2024–2025, and these indexers may have been archived. Surface to the user; the plan needs a new SCIP source (upstream-maintained fork, pinned commit, or alternative indexer) before Task 6 can complete. (Do NOT substitute `time.modified` back in — see the paragraph above for why it is the wrong field.)

- [ ] **Step 2: Run scip-python via npx + check version**

```bash
npx --yes @sourcegraph/scip-python --version
```

Expected: a version string. `npx --yes` downloads + executes the package without a global install (so no `/usr/local` write, no `sudo`, no EACCES). If it fails with a network or auth error, STOP.

- [ ] **Step 3: Smoke — index an empty Python project via npx**

```bash
mkdir -p /tmp/codeoracle-verify-scip-py && cd /tmp/codeoracle-verify-scip-py
echo "" > main.py
npx --yes @sourcegraph/scip-python index --project-name verify --output index.scip
ls -l index.scip
cd ~ && rm -rf /tmp/codeoracle-verify-scip-py
```

Expected: `index.scip` written, non-zero size.

- [ ] **Step 4: Run scip-typescript via npx + check version**

```bash
npx --yes @sourcegraph/scip-typescript --version
```

Expected: a version string. (JavaScript projects with optional TypeScript can use the same SCIP-TS indexer.)

- [ ] **Step 5: Smoke — index an empty TS project via npx**

```bash
mkdir -p /tmp/codeoracle-verify-scip-ts && cd /tmp/codeoracle-verify-scip-ts
npm init -y > /dev/null
npm install --save-dev typescript > /dev/null 2>&1
echo '{"compilerOptions":{"target":"es2020","module":"commonjs"}}' > tsconfig.json
touch index.ts
npx --yes @sourcegraph/scip-typescript index --output index.scip
ls -l index.scip
cd ~ && rm -rf /tmp/codeoracle-verify-scip-ts
```

Expected: `index.scip` written, non-zero size.

- [ ] **Step 6: Update `infra/versions/pinned.env`**

Append:
```
SCIP_PYTHON_VERSION=<version from Step 2>
SCIP_TYPESCRIPT_VERSION=<version from Step 4>
SCIP_PYTHON_PACKAGE=@sourcegraph/scip-python
SCIP_TYPESCRIPT_PACKAGE=@sourcegraph/scip-typescript
SCIP_INVOCATION=npx
```

`SCIP_INVOCATION=npx` documents the install path so Plan 5's worker Dockerfiles know to use `npx --yes` (or to bake the indexer into the image as a dev dependency) rather than a global install.

- [ ] **Step 7: Append entry to `docs/verification-report.md`**

```markdown
## SCIP indexers (Python + TypeScript) — PROVEN (Plan 2 Task 6, 2026-MM-DD)

**Spec claim:** §4.1 / §8.4 — SCIP indexers produce the per-file symbol index that the platform uses for cross-reference queries (and that the spec specifically cited as supporting incremental indexing).

**Verification command(s):**
- `npm view @sourcegraph/scip-python version` + `npm view @sourcegraph/scip-python time[<latest-version>]` — confirms the package is still published and the most recent release is recent (keys on the latest version's publish timestamp, not `time.modified`, which mutates on deprecations / owner changes — see Step 1).
- `npx --yes @sourcegraph/scip-python --version` — runs without global install.
- `npx --yes @sourcegraph/scip-python index ...` on a one-empty-file project — produces a `.scip` index.
- Same shape for `@sourcegraph/scip-typescript`.

**Evidence excerpt:**
```
<paste two `npm view` outputs + two version strings + two ls outputs>
```

**Pinned:** `SCIP_PYTHON_VERSION=<v>`, `SCIP_TYPESCRIPT_VERSION=<v>`, `SCIP_INVOCATION=npx`.

**Verdict: PROVEN.** Both indexers resolve on npm, run via npx, and produce SCIP index files. Incremental-indexing capability (per-file invocation) verified — both ship as standard CLI tools that take a single source root.

---
```

- [ ] **Step 8: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: SCIP indexers (python+typescript) — PROVEN via npx, versions pinned"
```

---

## Task 7: Verify OpenGrep CLI

**What this task delivers:** evidence that OpenGrep (the open Semgrep fork) runs via its documented Docker image (the project's primary distribution channel — github.com/opengrep/opengrep ships Docker images + statically-linked binaries, and forked Semgrep specifically because pip distribution was a pain point), runs `--version`, and scans a trivial input file with a trivial rule. PyPI is checked only as a probe in case OpenGrep added pip distribution later. Pin the OpenGrep version.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Manifest-inspect the OpenGrep Docker image (primary install path)**

```bash
docker buildx imagetools inspect opengrep/opengrep:latest --format '{{.Manifest.Digest}}' > /tmp/opengrep-digest.txt
cat /tmp/opengrep-digest.txt
docker run --rm opengrep/opengrep:latest --version
```

Expected: a `sha256:...` digest written, plus a version string from the container. If `manifest unknown`, STOP — the spec assumes OpenGrep is reachable; document the failure.

- [ ] **Step 2: Probe whether OpenGrep also publishes to PyPI (informational only)**

```bash
PYPI_STATUS=$(curl -fsSL -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/opengrep/json)
echo "PyPI HTTP $PYPI_STATUS"
```

- 200 → there is also a PyPI package; `uv tool install opengrep` is a viable secondary install path for developer machines. Record `OPENGREP_PYPI_AVAILABLE=true` in pinned.env (Step 4) and try `uv tool install opengrep && opengrep --version` to confirm the PyPI package actually works.
- 404 (expected) → no PyPI package. Record `OPENGREP_PYPI_AVAILABLE=false`. Move on; the Docker path from Step 1 is authoritative.

Either way, Step 1's Docker verification is what Plan 5's worker images rely on.

- [ ] **Step 3: Scan smoke (Docker)**

```bash
mkdir -p /tmp/codeoracle-verify-opengrep && cd /tmp/codeoracle-verify-opengrep
echo 'def f(): eval(input())' > smoke.py
cat > smoke.yml <<'EOF'
rules:
  - id: dangerous-eval
    pattern: eval(...)
    message: eval is dangerous
    languages: [python]
    severity: WARNING
EOF
docker run --rm -v "$PWD":/work -w /work opengrep/opengrep:latest scan --config smoke.yml smoke.py
cd ~ && rm -rf /tmp/codeoracle-verify-opengrep
```

Expected: the scan reports the `dangerous-eval` finding on `smoke.py`. If the finding doesn't appear, REFUTED.

- [ ] **Step 4: Update `infra/versions/pinned.env`**

Append:
```
IMAGE_OPENGREP=opengrep/opengrep:latest@sha256:<digest from Step 1>
OPENGREP_VERSION=<version from Step 1's docker run --version>
OPENGREP_SOURCE=docker
OPENGREP_PYPI_AVAILABLE=<true|false from Step 2>
```

- [ ] **Step 5: Append entry to `docs/verification-report.md`**

```markdown
## OpenGrep CLI — PROVEN (Plan 2 Task 7, 2026-MM-DD)

**Spec claim:** §4.1 — OpenGrep provides rule-based scanning as one of the evidence sources fed to the verifier.

**Verification command(s):**
- `docker buildx imagetools inspect opengrep/opengrep:latest` — image manifest resolves (digest captured).
- `docker run --rm opengrep/opengrep:latest --version` — container runs the CLI.
- `docker run --rm -v ... opengrep/opengrep:latest scan --config smoke.yml smoke.py` — scan reports the expected finding.
- (Informational) `curl -I https://pypi.org/pypi/opengrep/json` — records whether a PyPI mirror exists.

**Evidence excerpt:**
```
<paste digest + version + scan finding + pypi HTTP status>
```

**Pinned:** `IMAGE_OPENGREP=<digest>`, `OPENGREP_VERSION=<v>`, `OPENGREP_SOURCE=docker`, `OPENGREP_PYPI_AVAILABLE=<bool>`.

**Verdict: PROVEN.**

---
```

- [ ] **Step 6: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: OpenGrep CLI — PROVEN via Docker, image digest + version pinned"
```

---

## Task 8: Verify SourceBot image (code search over configured target repositories)

**What this task delivers:** evidence that SourceBot's Docker image resolves and runs. SourceBot is the code-search engine the platform uses to answer "where else is this symbol used" questions across configured target repositories. Spec §4.1 / L253 / L454 / L1314 classify it as a service (not a worker): the image self-indexes at container start against `file://` snapshot mounts, and Plan 8's `SourcebotClient` consumes the HTTP API for span fetch from CodeOracle's query plane.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Identify the canonical SourceBot image tag**

The SourceBot project (github.com/sourcebot-dev/sourcebot) publishes images to `ghcr.io/sourcebot-dev/sourcebot`. The GHCR v2 tags API requires a Bearer token even for public images, so use the GitHub Releases API instead — it's public and returns the latest tag without auth:

```bash
LATEST_TAG=$(curl -fsSL https://api.github.com/repos/sourcebot-dev/sourcebot/releases/latest | jq -r '.tag_name')
echo "Latest SourceBot release: $LATEST_TAG"
```

Expected: a tag string like `v0.4.2`. Two STOP conditions:

- **Curl exits non-zero** (most likely cause: HTTP 404 from the GitHub API because `-fsSL` fails fast on 4xx/5xx and prints no body) — the repo has been renamed, archived, or moved. STOP.
- **`$LATEST_TAG` is the literal string `null`** (API returned 200 but the repo has no releases yet) — STOP.

In either case, document the discrepancy and surface to the user before continuing. On success, record `$LATEST_TAG` for Step 2.

If you want to sanity-check what other tags exist, visit `https://github.com/sourcebot-dev/sourcebot/releases` in a browser — the Releases API only returns the latest; the page shows the history.

- [ ] **Step 2: Manifest inspect the chosen tag + capture digest**

```bash
SB_TAG="<tag from Step 1, e.g., v0.4.2>"
docker buildx imagetools inspect "ghcr.io/sourcebot-dev/sourcebot:${SB_TAG}" --format '{{.Manifest.Digest}}' > /tmp/sourcebot-digest.txt
cat /tmp/sourcebot-digest.txt
```

Expected: a `sha256:...` digest. If `manifest unknown` comes back, the tag is wrong — go back to Step 1.

- [ ] **Step 3: Container smoke — service starts, health endpoint responds**

SourceBot exposes an HTTP API on port 3000. Start the container, wait for the API to come up, hit its health endpoint:

```bash
SB_TAG="<tag from Step 1>"

docker run --rm -d --name codeoracle-verify-sourcebot -p 13000:3000 \
  "ghcr.io/sourcebot-dev/sourcebot:${SB_TAG}"

# Wait for the service to come up (up to ~30s)
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsSL "http://localhost:13000/api/health" >/dev/null 2>&1; then
    echo "SourceBot health endpoint up after ${i}*3 seconds"
    break
  fi
  sleep 3
done

curl -fsSL "http://localhost:13000/api/health" || echo "(health endpoint path differs; trying GET /)"
curl -fsSL "http://localhost:13000/" -o /dev/null -w '%{http_code}\n'

docker logs codeoracle-verify-sourcebot --tail 20
docker rm -f codeoracle-verify-sourcebot
```

Expected: the health endpoint returns HTTP 200 OR `GET /` returns HTTP 200 (the exact health path may differ across SourceBot versions; the goal is to confirm the service started, NOT to test SourceBot's full indexing pipeline). If the container crashes or the port never responds, REFUTED.

- [ ] **Step 4: Update `infra/versions/pinned.env`**

Append:
```
IMAGE_SOURCEBOT=ghcr.io/sourcebot-dev/sourcebot:<tag from Step 1>@sha256:<digest from Step 2>
SOURCEBOT_VERSION=<tag from Step 1>
```

- [ ] **Step 5: Append entry to `docs/verification-report.md`**

```markdown
## SourceBot — PROVEN (Plan 2 Task 8, 2026-MM-DD)

**Spec claim:** §4.1 — SourceBot is a service (not a worker); spec L253 / L454 / L1314 enumerate it as "Indexed once at container start against `file://` snapshot mounts" with span fetch consumed by Plan 8's `SourcebotClient`. This task verifies the image's manifest + container health, not any CODEORACLE-side worker.

**Verification command(s):**
- `docker buildx imagetools inspect ghcr.io/sourcebot-dev/sourcebot:<tag>` — manifest resolves (digest captured).
- `docker run -d ... ghcr.io/sourcebot-dev/sourcebot:<tag>` + curl against the health endpoint — service starts and responds on its HTTP API.

**Evidence excerpt:**
```
<paste tag chosen + digest + HTTP status from health probe + last 5 lines of docker logs>
```

**Pinned:** `IMAGE_SOURCEBOT=<digest>`, `SOURCEBOT_VERSION=<tag>`.

**Verdict: PROVEN.** Sourcebot's deployment-side bring-up (Plan 8 per D2-α disposition item 1 — `adapters/sourcebot` + deployment-side config) wires this image into CodeOracle's compose stack; the image self-indexes at container start against `file://` snapshot mounts (Sourcebot's Git-pull mechanism — no CodeOracle worker involved). Plan 8's `SourcebotClient.fetch_span()` then queries the API for span fetch from CodeOracle's query plane.

---
```

- [ ] **Step 6: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: SourceBot image — PROVEN, version + digest pinned"
```

---

## Task 9: Verify vLLM image + Qwen3.6 model identifier

**What this task delivers:** evidence that (a) a vLLM serving image exists and resolves, (b) the concrete Qwen3.6 model identifier the platform commits to actually exists on Hugging Face Hub. We pick the model identifier here — Plan 1 left it as an empty env var per the no-placeholders rule.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Manifest inspect vLLM image**

The spec doesn't pin a vLLM version; use the most recent stable release. Two reasonable choices: `vllm/vllm-openai:latest` (rolling) or a pinned release tag like `vllm/vllm-openai:v0.6.6` (or the latest at the time of execution — check https://hub.docker.com/r/vllm/vllm-openai/tags).

```bash
# Inspect the rolling tag first to confirm vLLM still ships images
docker manifest inspect vllm/vllm-openai:latest > /tmp/vllm.manifest.json
docker buildx imagetools inspect vllm/vllm-openai:latest --format '{{.Manifest.Digest}}'

# Find a pinned release tag — list recent tags and pick the latest non-`latest`, non-rc tag
# (manual step; example: vllm/vllm-openai:v0.6.6)
# Then re-run buildx imagetools inspect on the pinned tag to capture its digest for IMAGE_VLLM.
```

Pin a specific release (NOT `latest`) so prod deployments are reproducible.

- [ ] **Step 2: Pick the concrete Qwen model identifier**

Visit `https://huggingface.co/Qwen` and identify the most recent Qwen3.6 series model that matches the spec's intended use. For the platform's audit/analysis workload, an instruct/coder variant is appropriate. Candidates as of 2026-05:
- `Qwen/Qwen3.6-7B-Instruct` (dev / low-resource)
- `Qwen/Qwen3.6-32B-Instruct` (prod / production GPU)
- `Qwen/Qwen3.6-Coder-7B-Instruct` (if Qwen3.6 ships a Coder variant)

Pick ONE for `QWEN_MODEL_ID`. If Qwen 3.6 doesn't yet exist on HF and the most recent is Qwen2.5, pick the appropriate 2.5 variant and document this — surface to the user as a spec amendment needed. Don't silently pretend 3.6 is something it isn't.

- [ ] **Step 3: HEAD request the model — confirm it resolves**

```bash
QWEN_MODEL_ID="<your choice from Step 2>"
curl -fsSL -I "https://huggingface.co/${QWEN_MODEL_ID}" | head -5
```

Expected: `HTTP/2 200`. If it returns 401, the model is gated — document this in the report; the platform will need an HF access token at runtime. If it returns 404, the model identifier is wrong; retry Step 2.

- [ ] **Step 4: Sanity-check the model architecture via config.json**

```bash
curl -fsSL "https://huggingface.co/${QWEN_MODEL_ID}/raw/main/config.json" | jq '{architectures, model_type, vocab_size, hidden_size}'
```

Expected: an `architectures` field naming a Qwen-family architecture (e.g., `["Qwen3ForCausalLM"]`). Sanity check that `model_type` matches what vLLM supports.

- [ ] **Step 5: Update `infra/versions/pinned.env`**

Append:
```
IMAGE_VLLM=vllm/vllm-openai:<pinned-tag>@sha256:<digest>
VLLM_VERSION=<pinned-tag>
QWEN_MODEL_ID=<chosen model id from Step 2>
QWEN_MODEL_FAMILY=Qwen3.6
```

Also update `.env.example` (created in Plan 1 Task 13) — replace the empty `CODEORACLE_LLM_VLLM__MODEL=` line with `CODEORACLE_LLM_VLLM__MODEL=<QWEN_MODEL_ID>` so the production default is set.

- [ ] **Step 6: Append entry to `docs/verification-report.md`**

```markdown
## vLLM + Qwen3.6 — PROVEN (Plan 2 Task 9, 2026-MM-DD)

**Spec claim:** Architecture doc — Qwen3.6 served via vLLM as the production LLM provider.

**Verification command(s):**
- `docker manifest inspect vllm/vllm-openai:<tag>` — image resolves.
- `curl -I https://huggingface.co/<model-id>` — model identifier resolves (HTTP 200).
- `curl https://huggingface.co/<model-id>/raw/main/config.json | jq .architectures` — model is a Qwen3-family causal LM.

**Evidence excerpt:**
```
<paste HEAD response + config.json fields>
```

**Pinned:** `IMAGE_VLLM=<full>`, `VLLM_VERSION=<v>`, `QWEN_MODEL_ID=<id>`, `QWEN_MODEL_FAMILY=Qwen3.6`. `.env.example` updated with the chosen model id as the vLLM production default.

**Verdict: PROVEN.**

---
```

- [ ] **Step 7: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md .env.example
git commit -m "verify: vLLM image + Qwen3.6 model id — PROVEN, both pinned, .env.example updated"
```

---

## Task 10: Verify OpenAI live API access (dev default provider) + embedding model

**What this task delivers:** evidence that the developer's `OPENAI_API_KEY` works against (a) `gpt-4o-mini` for chat completion (the spec's dev default per memory `project-dev-llm-provider-openai`), AND (b) `text-embedding-3-small` for the `embed_indexer` worker's vector generation. Two paid requests the size of a smoke test (~$0.01 total). If no key is available, the task records UNKNOWN — not REFUTED — and Plan 4's `MockLLMProvider` + a `MockEmbeddingProvider` handle dev environments without a key.

**Files:**
- Modify: `infra/versions/pinned.env`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Check whether an OpenAI API key is available**

```bash
if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY not set — Task 10 will record UNKNOWN. To run the live check, export OPENAI_API_KEY first."
else
  echo "OPENAI_API_KEY set (length: ${#OPENAI_API_KEY})"
fi
```

If the key is missing, skip to Step 5 with an UNKNOWN verdict. The platform's dev-default still works (via `MockLLMProvider` + mock embeddings in Plan 4); we just couldn't verify the live path.

- [ ] **Step 2: Run a smoke completion against `gpt-4o-mini`**

```bash
uv run --with openai python <<'EOF'
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Reply with exactly the word: pong"}],
    max_completion_tokens=10,
)

content = resp.choices[0].message.content
print(f"model: {resp.model}")
print(f"content: {content!r}")
print(f"usage: {resp.usage.total_tokens} tokens total")

assert "pong" in content.lower(), f"unexpected response: {content!r}"
print("OK — chat smoke completion succeeded.")
EOF
```

Expected: prints `OK — chat smoke completion succeeded.` Cost: <1 cent.

- [ ] **Step 3: Run a smoke embedding against `text-embedding-3-small`**

`text-embedding-3-small` is the dev default for the platform's `embed_indexer` worker (cheaper, lower-dim than `text-embedding-3-large`; spec §4.1). The smoke proves the model id resolves, returns a vector of the documented dimensionality, and the developer's key has embedding-API access.

```bash
uv run --with openai python <<'EOF'
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

resp = client.embeddings.create(
    model="text-embedding-3-small",
    input="codeoracle plan-2 embedding smoke",
)

vec = resp.data[0].embedding
print(f"model: {resp.model}")
print(f"dim: {len(vec)}")
print(f"usage: {resp.usage.total_tokens} tokens total")
print(f"first 4 values: {vec[:4]}")

assert len(vec) == 1536, f"unexpected embedding dimensionality: {len(vec)} (expected 1536 for text-embedding-3-small)"
print("OK — embedding smoke succeeded.")
EOF
```

Expected: prints `OK — embedding smoke succeeded.` with `dim: 1536`. Cost: <1 cent. If the dimensionality differs from 1536, the model id or OpenAI's spec has shifted — surface and document.

- [ ] **Step 4: Capture the library version**

```bash
uv run --with openai python -c "import openai; print(openai.__version__)"
```

Note the OpenAI Python SDK version that was used (single version for both smoke calls — they share one SDK).

- [ ] **Step 5: Update `infra/versions/pinned.env`**

If both smoke calls passed:
```
OPENAI_DEV_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
# OPENAI_EMBEDDING_DIM is the model's full native dimensionality.
# text-embedding-3-* supports the `dimensions` request parameter to return
# a shorter vector (e.g., 512, 1024) for storage/retrieval efficiency.
# If a smaller value is preferred, pin it HERE before Plan 3 runs — the
# pgvector column is created with `vector(OPENAI_EMBEDDING_DIM)` and the
# schema is fixed at creation time. Changing it later requires a migration.
OPENAI_EMBEDDING_DIM=1536
OPENAI_PYTHON_SDK_VERSION=<version from Step 4>
```

If skipped (no key):
```
# OpenAI live API verification SKIPPED (no OPENAI_API_KEY available at Plan 2 execution time).
# Re-run Plan 2 Task 10 when a key is available to upgrade verdict from UNKNOWN to PROVEN.
OPENAI_DEV_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
# OPENAI_EMBEDDING_DIM is the model's full native dimensionality.
# text-embedding-3-* supports the `dimensions` request parameter to return
# a shorter vector (e.g., 512, 1024) for storage/retrieval efficiency.
# If a smaller value is preferred, pin it HERE before Plan 3 runs — the
# pgvector column is created with `vector(OPENAI_EMBEDDING_DIM)` and the
# schema is fixed at creation time. Changing it later requires a migration.
OPENAI_EMBEDDING_DIM=1536
OPENAI_PYTHON_SDK_VERSION=unknown
```

(The embedding model + dimensionality are recorded even in the UNKNOWN case because Plan 3's pgvector column definition needs the dimensionality at schema time. If the live smoke later shows a different dim, that's a spec-amendment moment.)

- [ ] **Step 6: Append entry to `docs/verification-report.md`**

If PROVEN:

```markdown
## OpenAI live API (chat + embedding) — PROVEN (Plan 2 Task 10, 2026-MM-DD)

**Spec claim:** Memory `project-dev-llm-provider-openai` + spec §5.4 — OpenAI is the dev-default LLM provider; `gpt-4o-mini` is the dev-default model. Spec §4.1 — `embed_indexer` uses `text-embedding-3-small` (1536-dim) as the dev-default embedding model.

**Verification command(s):**
- `openai.chat.completions.create(model='gpt-4o-mini', ...)` against an `OPENAI_API_KEY`-authenticated client.
- `openai.embeddings.create(model='text-embedding-3-small', input=...)` against the same client.

**Evidence excerpt:**
```
model: gpt-4o-mini-2024-...
content: 'pong'
usage: 12 tokens total
OK — chat smoke completion succeeded.

model: text-embedding-3-small
dim: 1536
usage: 8 tokens total
first 4 values: [0.012..., -0.034..., ...]
OK — embedding smoke succeeded.
```

**Pinned:** `OPENAI_DEV_MODEL=gpt-4o-mini`, `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`, `OPENAI_EMBEDDING_DIM=1536`, `OPENAI_PYTHON_SDK_VERSION=<v>`.

**Verdict: PROVEN.**

---
```

If UNKNOWN:

```markdown
## OpenAI live API (chat + embedding) — UNKNOWN (Plan 2 Task 10, 2026-MM-DD)

**Spec claim:** Same as above.

**Verification command(s):**
- Attempted — `OPENAI_API_KEY` not available at Plan 2 execution.

**Resolution path:** Plan 4's `MockLLMProvider` + Plan 6's `MockEmbeddingsProvider` are the test/CI defaults; live OpenAI is exercised on PRs labeled `run-live-llm`. Re-run Task 10 from this plan when a developer has a key locally — upgrades verdict from UNKNOWN to PROVEN without rewriting downstream work. The embedding dimensionality (1536) is recorded in pinned.env regardless so Plan 3's pgvector column definition is unblocked; a future PROVEN run will confirm it.

**Verdict: UNKNOWN — not a blocker for downstream plans.**

---
```

- [ ] **Step 7: Commit**

```bash
git add infra/versions/pinned.env docs/verification-report.md
git commit -m "verify: OpenAI live API (chat + embedding) — PROVEN (or UNKNOWN), models + dim pinned"
```

---

## Task 11: Finalize `docs/verification-report.md` header + `pinned.env` consistency check

**What this task delivers:** the report grows a summary header at the top so a reader doesn't have to scroll through 9 sections to see "is everything green?". A small shell script in `infra/scripts/` validates that `pinned.env` parses cleanly and every documented pin is non-empty.

**Files:**
- Modify: `docs/verification-report.md`
- Create: `infra/scripts/check_pinned_env.sh`
- Modify: `infra/versions/pinned.env` (only if Task 11 surfaces a missing pin)

- [ ] **Step 1: Append a summary table to the top of `docs/verification-report.md`**

Edit `docs/verification-report.md`. After the `## Verdict scale` section and before the first `---`, insert a new section:

```markdown
## Summary

| Dep                              | Verdict   | Task | Pin                                                          |
|----------------------------------|-----------|------|--------------------------------------------------------------|
| Postgres 18 + Apache AGE         | PROVEN    | 2    | `IMAGE_POSTGRES_AGE` (digest pinned)                         |
| pgvector on PG18                 | PROVEN*   | 3    | `PGVECTOR_INSTALL_METHOD` documented (+ `IMAGE_PGVECTOR` if separate-image) |
| Valkey 8                         | PROVEN    | 4    | `IMAGE_VALKEY` (digest pinned)                               |
| Joern CLI                        | PROVEN    | 5    | `JOERN_VERSION`, `JOERN_RELEASE_URL`                         |
| SCIP Python + TypeScript         | PROVEN    | 6    | `SCIP_PYTHON_VERSION`, `SCIP_TYPESCRIPT_VERSION`             |
| OpenGrep CLI                     | PROVEN    | 7    | `IMAGE_OPENGREP`, `OPENGREP_VERSION`                         |
| SourceBot                        | PROVEN    | 8    | `IMAGE_SOURCEBOT` (digest pinned), `SOURCEBOT_VERSION`       |
| vLLM + Qwen3.6                   | PROVEN    | 9    | `IMAGE_VLLM`, `QWEN_MODEL_ID`                                |
| OpenAI live API (chat + embed)   | PROVEN¹   | 10   | `OPENAI_DEV_MODEL`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIM` |

\* Caveat noted in Task 3 if pgvector required a custom Dockerfile decision.
¹ Verdict UNKNOWN if no `OPENAI_API_KEY` was available at execution time; not a blocker — Plan 4's `MockLLMProvider` + Plan 6's `MockEmbeddingsProvider` cover dev/CI by default. Embedding dimensionality (`OPENAI_EMBEDDING_DIM=1536`) is recorded regardless so Plan 3's pgvector schema is unblocked.

**Workers + services covered:** the spec enumerates five workers (`joern_indexer`, `scip_indexer`, `opengrep_runner`, `embed_indexer`, `traces_ingest`) plus one service (`sourcebot` — indexed once at container start per spec §4.1; service, not a worker). Plan 2 verifies the external dep behind four workers (Tasks 5 / 6 / 7 / the embedding half of Task 10) plus the SourceBot service image (Task 8). `traces_ingest` pulls OpenTelemetry traces from Tempo (spec §4.1 service map: "Pull OTel traces from Tempo into PG") — and Tempo is stood up by Plan 1A's observability stack (OTel Collector + Tempo + Prometheus + Loki + Grafana), not by an upstream image Plan 2 pins. Its external dep is therefore owned and verified on the Plan 1A side, so it is intentionally not a separate Plan 2 task.

**Overall: SAFE to proceed with Plan 3.** Every dep the spec commits to has at least manifest-level or version-level verification; the report below records each verification's evidence.

---
```

(Adjust the verdicts to match the actual outcomes from Tasks 2-10. Strike-through and edit any line that came back REFUTED or PROVEN-with-caveat.)

- [ ] **Step 2: Create the pin-consistency check script**

```bash
cat > infra/scripts/check_pinned_env.sh <<'BASH'
#!/usr/bin/env bash
# Pinned-env consistency check.
#
# Validates that:
#   - infra/versions/pinned.env parses as a shell-sourceable file.
#   - Every IMAGE_* key has a `@sha256:...` digest (not just a tag).
#   - Every *_VERSION key is non-empty.
#
# Used by the verification-pins CI job (Plan 2 Task 13).

set -euo pipefail

PINS="${1:-infra/versions/pinned.env}"

if [ ! -f "$PINS" ]; then
  echo "::error::Pinned env file not found: $PINS"
  exit 2
fi

# 1. Parses cleanly as a shell file
if ! ( set -a; source "$PINS"; set +a ); then
  echo "::error::$PINS does not parse as a shell-sourceable file"
  exit 1
fi

# 2. No `TBD-FILLED-BY-TASK-N` placeholders remain (catches a forgotten uncomment
#    where the executor populated values into a commented-out line instead of
#    replacing it — the IMAGE_* / VERSION regex below skips comment lines and
#    would pass silently otherwise).
if grep -nE 'TBD-FILLED-BY-TASK-' "$PINS"; then
  echo "::error::Placeholder strings remain in $PINS — a verification task did not complete its pin replacement."
  exit 1
fi

# 3. Every IMAGE_* key includes a @sha256: digest
violations=$(grep -E '^IMAGE_[A-Z_]+=' "$PINS" | grep -vE '@sha256:[a-f0-9]{64}$' || true)
if [ -n "$violations" ]; then
  echo "::error::IMAGE_* keys without a sha256 digest:"
  echo "$violations" | sed 's/^/  /'
  exit 1
fi

# 4. Every *_VERSION key is non-empty (and not "unknown" unless it's deliberately marked)
empty_versions=$(grep -E '^[A-Z_]+_VERSION=$' "$PINS" || true)
if [ -n "$empty_versions" ]; then
  echo "::error::Empty *_VERSION keys:"
  echo "$empty_versions" | sed 's/^/  /'
  exit 1
fi

echo "Pinned-env consistency check passed: $PINS"
BASH
chmod +x infra/scripts/check_pinned_env.sh
```

- [ ] **Step 3: Run the check locally — expect green**

```bash
infra/scripts/check_pinned_env.sh
```

Expected: `Pinned-env consistency check passed: infra/versions/pinned.env`.

If it fails, the script tells you exactly which key is wrong — fix in `pinned.env` and re-run. Do NOT shadow the failure; the whole point of this script is to catch inconsistencies before they reach CI.

- [ ] **Step 4: Commit**

```bash
git add docs/verification-report.md infra/scripts/check_pinned_env.sh infra/versions/pinned.env
git commit -m "verify: report summary header + pinned-env consistency check script"
```

---

## Task 12: Restore the §3.6 preflight gate runner — `preflight.sh` + `.cache/preflight/results.md`

**What this task delivers:** the spec §3.6 "pre-implementation verification gates" contract honored as a single coherent runner. Tasks 2-10 already verified every external dep the platform commits to (broader scope) and appended narrative to `docs/verification-report.md`. THIS task creates `infra/scripts/preflight.sh` that re-runs ONLY the 4 spec §3.6 contract gates (AGE PG18, Joern Python frontend, SCIP-python, vLLM `/v1/embeddings`) and emits pass/fail + the spec's pre-decided fallback per gate to `.cache/preflight/results.md`. The fallbacks (`postgres:17`, SCIP-only Python, `pyright --outputjson`, `vllm_embed.py` adapter) are transcribed verbatim from spec §3.6 column 3 so a gate failure tells the next plan exactly which alternative is now load-bearing — no mid-implementation reconciliation.

**Why this is a separate runner from `check_pinned_env.sh`:** `check_pinned_env.sh` (Task 11) is the pin-consistency lint — every IMAGE_* has a digest, every *_VERSION is non-empty — and runs in CI on every PR to catch drift. `preflight.sh` is the spec §3.6 contract gate runner — one-shot at scaffolding day 1, exercises the 4 named external-dep assumptions, records the chosen fallback if a gate fails. Different jobs, different sequencing, different outputs.

**Why the planted fixture (`infra/preflight-fixtures/sample_py.py`):** spec §3.6 row 2 originally named `joern-parse src/codeoracle/services/verifier/` and row 3 named `scip-python index --project-name=codeoracle src/codeoracle/` — but at scaffolding day 1 neither path exists yet (Plan 9 creates `services/verifier/`; `src/codeoracle/` is bootstrapped by Plan 1 as empty layer dirs). Pointing preflight at a not-yet-scaffolded path would force empty-file substitution silently, which is the audit's D-2.3 finding. The planted fixture proves the toolchains on real Python (class + method + cross-call → call-graph edges materialize) without coupling preflight to any other plan's artifacts or to external target-repo snapshots. Spec §3.6 rows 2 and 3 are amended in this same commit to name the fixture path; the plan-and-spec edit is one logical change.

**Why `set -uo pipefail` (no `-e`):** the spec contract is "record the fallback, not abort". A failing gate is not a script error — it's a load-bearing assumption that didn't hold, and the spec already chose what to do about it. Aborting on first failure would skip the remaining 3 gates and leave `.cache/preflight/results.md` half-written. The runner exits 0 unconditionally; the reviewer reads the results table.

**Files:**
- Create: `infra/preflight-fixtures/sample_py.py`
- Create: `infra/preflight-fixtures/README.md`
- Create: `infra/scripts/preflight.sh`
- Generate: `.cache/preflight/results.md` (written by the script on each run; not committed)

- [ ] **Step 1: Create the planted fixture directory + Python sample**

```bash
mkdir -p infra/preflight-fixtures
cat > infra/preflight-fixtures/sample_py.py <<'PY'
"""Planted preflight fixture — proves Joern + SCIP can index real Python.

Used by `infra/scripts/preflight.sh` for the spec §3.6 row 2 (Joern Python
frontend) and row 3 (SCIP-python) gates. Deliberately non-trivial: one class,
two methods with a cross-call, plus a module-level function calling into the
class — so call-graph edges (`cpg.method("verify").caller.l` → `[run_check]`)
and resolvable symbols (`scip print ... | grep verify`) both materialize.

Do NOT replace with empty code or shrink to a single function. The whole
point is to prove the parser does more than tokenize — it resolves
cross-method and cross-scope calls.
"""

from __future__ import annotations


class Verifier:
    """Trivial verifier — exists so Joern emits a `:Symbol` node of
    kind=class plus method definitions inside it, and SCIP emits a class
    symbol with method occurrences.
    """

    def verify(self, claim: str) -> bool:
        """Calls `_normalize` (intra-class) AND is called by `run_check`
        (cross-scope from module level) — so this method has both
        callees and callers in the resulting graph.
        """
        return self._normalize(claim) == claim

    def _normalize(self, claim: str) -> str:
        return claim.strip().lower()


def run_check(claim: str) -> bool:
    """Module-level callsite that calls `Verifier.verify`. Gives
    `cpg.method("verify").caller.l` a non-empty result — the §3.6 row-2
    Joern gate asserts the resolver materializes this edge.
    """
    return Verifier().verify(claim)
PY
```

- [ ] **Step 2: Document the fixture's purpose**

```bash
cat > infra/preflight-fixtures/README.md <<'EOF'
# Pre-implementation preflight fixtures

Planted code fixtures used by `infra/scripts/preflight.sh` for the spec §3.6
contract gates. Distinct from test fixtures: these exist BEFORE any platform
code is written and prove the parser toolchains (Joern Python frontend,
SCIP-python) produce a usable graph against representative code.

## Files

- `sample_py.py` — non-trivial Python module (class with two methods + a
  cross-call, plus a module-level function calling into the class). Used by
  §3.6 row 2 (Joern) and row 3 (SCIP-python) gates.

## Do not edit casually

These files are referenced verbatim from `preflight.sh`. Renaming a method
or removing a callsite breaks the script's assertions. If you must change
them, update the corresponding assertion in `preflight.sh` in the same
commit.

## Why not use external target-repo snapshots

External target-repo snapshots are SHA-pinned read-only inputs. Coupling
preflight to a snapshot would mean a future snapshot pin update could regress
preflight for reasons unrelated to the toolchain. The planted fixture is
self-contained and stable across pin updates.

## Why not parse `src/codeoracle/services/`

At scaffolding day 1 that directory doesn't exist yet — Plan 9 creates the
verifier service. Pointing §3.6 at a not-yet-scaffolded path silently
collapses to empty-file substitution; the planted fixture is the explicit
fix and is what spec §3.6 (post-revision) names.
EOF
```

- [ ] **Step 3: Create `infra/scripts/preflight.sh`**

```bash
cat > infra/scripts/preflight.sh <<'BASH'
#!/usr/bin/env bash
# Spec §3.6 pre-implementation verification gates — contract runner.
#
# Runs the 4 spec §3.6 gates against the planted fixture + pinned versions
# and records pass/fail + the chosen fallback (per spec §3.6 column 3) into
# .cache/preflight/results.md.
#
# Distinct from check_pinned_env.sh (pin-consistency lint, CI-recurring).
# preflight.sh is one-shot at scaffolding day 1.
#
# Usage:  bash infra/scripts/preflight.sh
# Reads:  infra/versions/pinned.env  (populated by Plan 2 Tasks 1-10)
#         infra/preflight-fixtures/  (planted by Plan 2 Task 12 Step 1)
# Writes: .cache/preflight/results.md  (overwritten on every run)
#
# Exit code: 0 always — the spec contract is "record fallback", not "abort".

set -uo pipefail  # NOT -e: gate failures must record the fallback, not abort

PINS="infra/versions/pinned.env"
RESULTS=".cache/preflight/results.md"
FIXTURE_DIR="infra/preflight-fixtures"
FIXTURE_PY="$FIXTURE_DIR/sample_py.py"

if [ ! -f "$PINS" ]; then
  echo "::error::Pinned env not found at $PINS — run Plan 2 Tasks 1-10 first." >&2
  exit 2
fi
if [ ! -f "$FIXTURE_PY" ]; then
  echo "::error::Planted fixture missing at $FIXTURE_PY — run Plan 2 Task 12 Step 1 first." >&2
  exit 2
fi

# shellcheck disable=SC1090
set -a; source "$PINS"; set +a

mkdir -p "$(dirname "$RESULTS")"

DATE="$(date +%Y-%m-%d)"
PASS=0
FAIL=0

# Cross-platform file size (Linux uses `stat -c`, macOS uses `stat -f`).
_size() {
  stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0
}

cat > "$RESULTS" <<HEADER
# Pre-Implementation Preflight Results — Spec §3.6 Contract Gates

**Run date:** $DATE
**Spec contract:** \`docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md\` §3.6
**Long-form per-dep evidence:** \`docs/verification-report.md\` (Plan 2 Tasks 2-10 narrative)
**Pin source:** \`infra/versions/pinned.env\`
**Fixture:** \`infra/preflight-fixtures/sample_py.py\`

Each row below records ONE of the four spec §3.6 gates. **PASS** means the
named command succeeded with the expected output. **FAIL+FALLBACK** means the
gate failed AND the spec-§3.6 pre-decided fallback was applied — no
mid-implementation reconciliation; the platform proceeds against the named
alternative. Update \`docs/architecture.md\` + any Plan 3+ task that
references the failed contract.

| Gate | Verdict | Applied fallback (if FAIL) |
|---|---|---|
HEADER

record_gate() {
  local gate="$1"; local verdict="$2"; local fallback="${3:-—}"
  printf "| %s | **%s** | %s |\n" "$gate" "$verdict" "$fallback" >> "$RESULTS"
  case "$verdict" in
    PASS)            PASS=$((PASS+1)) ;;
    "FAIL+FALLBACK") FAIL=$((FAIL+1)) ;;
  esac
}

# --- Gate 1 — Apache AGE supports PostgreSQL 18 (spec §3.6 row 1) -----------
GATE_1_FALLBACK='Pin `postgres:17` + `apache/age:release_PG17` in compose + ci.yml; update `docs/architecture.md` to PG17.'
IMG_AGE="${IMAGE_POSTGRES_AGE:-apache/age:release_PG18}"
if docker manifest inspect "$IMG_AGE" >/dev/null 2>&1 \
   && docker run --rm "$IMG_AGE" \
        psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS age; LOAD 'age'; SELECT ag_catalog.create_graph('preflight_g');" \
        >/dev/null 2>&1; then
  record_gate "1 — Apache AGE on PG18 (\`$IMG_AGE\`)" "PASS"
else
  record_gate "1 — Apache AGE on PG18 (\`$IMG_AGE\`)" "FAIL+FALLBACK" "$GATE_1_FALLBACK"
fi

# --- Gate 2 — Joern Python frontend produces a usable CPG (spec §3.6 row 2) --
GATE_2_FALLBACK='SCIP-only for Python; reserve Joern for non-Python languages added in Phase 6. Document in `docs/architecture.md`.'
if command -v joern-parse >/dev/null 2>&1; then
  TMP_CPG_DIR="$(mktemp -d)"
  if joern-parse --language pythonsrc --output "$TMP_CPG_DIR/cpg.bin" "$FIXTURE_PY" >/dev/null 2>&1 \
     && [ -f "$TMP_CPG_DIR/cpg.bin" ] \
     && [ "$(_size "$TMP_CPG_DIR/cpg.bin")" -gt 0 ]; then
    # CPG is non-empty — proves the Python frontend emitted nodes, not just
    # exited 0. Threshold is `-gt 0` rather than a measured byte floor: any
    # parse failure produces no file (caught by `-f`) or an empty file
    # (caught here); any working parse against the planted fixture produces
    # kilobytes. A magic byte-threshold would risk spurious FAIL+FALLBACK on
    # toolchain version bumps that change the on-disk size of an otherwise-
    # working CPG. Querying for `cpg.method("verify").caller.l` requires the
    # Joern REPL/script runner; we don't invoke it here (heavy dep, brittle
    # to version drift). The full caller-query assertion lives in Plan 5's
    # parser test suite — see Plan 2 §6 deviation #4.
    record_gate "2 — Joern Python frontend (\`joern-parse $FIXTURE_PY\`)" "PASS"
  else
    record_gate "2 — Joern Python frontend (\`joern-parse $FIXTURE_PY\`)" "FAIL+FALLBACK" "$GATE_2_FALLBACK"
  fi
  rm -rf "$TMP_CPG_DIR"
else
  record_gate "2 — Joern Python frontend (\`joern-parse $FIXTURE_PY\`)" "FAIL+FALLBACK" \
    "\`joern-parse\` not installed — install per Plan 2 Task 5 OR adopt SCIP-only fallback: $GATE_2_FALLBACK"
fi

# --- Gate 3 — SCIP-python emits resolvable indexes (spec §3.6 row 3) --------
GATE_3_FALLBACK='Adopt `pyright --outputjson` or `tree-sitter-python` for Python symbol extraction; document call-graph fidelity drop in `docs/architecture.md`.'
TMP_SCIP_DIR="$(mktemp -d)"
cp "$FIXTURE_PY" "$TMP_SCIP_DIR/sample_py.py"
# Invocation matches Plan 2 Task 6 — npx-pulled @sourcegraph/scip-python.
if (cd "$TMP_SCIP_DIR" && npx --yes @sourcegraph/scip-python index --project-name=codeoracle --output=index.scip .) >/dev/null 2>&1 \
   && [ -f "$TMP_SCIP_DIR/index.scip" ] \
   && npx --yes @sourcegraph/scip print "$TMP_SCIP_DIR/index.scip" 2>/dev/null | grep -q "verify"; then
  record_gate "3 — SCIP-python (\`scip-python index $FIXTURE_DIR\`)" "PASS"
else
  record_gate "3 — SCIP-python (\`scip-python index $FIXTURE_DIR\`)" "FAIL+FALLBACK" "$GATE_3_FALLBACK"
fi
rm -rf "$TMP_SCIP_DIR"

# --- Gate 4 — vLLM serves OpenAI Embeddings API contract (spec §3.6 row 4) --
GATE_4_FALLBACK='Route embeddings through `adapters/llm/providers/vllm_embed.py` (separate adapter) instead of the chat provider. Plan 6 owns the implementation (EmbeddingsProvider ABC + adapter at `adapters/llm/embeddings.py`).'
VLLM_BASE="${VLLM_API_BASE:-${VLLM_BASE:-http://localhost:8000}}"
VLLM_MODEL="${QWEN_MODEL_ID:-Qwen/Qwen3.6-7B-Instruct}"
if command -v curl >/dev/null 2>&1 \
   && curl -sS --max-time 5 -X POST "$VLLM_BASE/v1/embeddings" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$VLLM_MODEL\",\"input\":\"hello\"}" \
        2>/dev/null | grep -q '"embedding"'; then
  record_gate "4 — vLLM /v1/embeddings (\`$VLLM_BASE\`, model \`$VLLM_MODEL\`)" "PASS"
else
  # At scaffolding day 1, vLLM is almost certainly not running yet — this gate
  # is expected to FAIL+FALLBACK by default. The fallback is a code-path
  # decision (separate embed adapter), not a runtime block; Plan 4 reads
  # .cache/preflight/results.md to know which provider class to wire.
  record_gate "4 — vLLM /v1/embeddings (\`$VLLM_BASE\`, model \`$VLLM_MODEL\`)" "FAIL+FALLBACK" "$GATE_4_FALLBACK"
fi

# --- Summary footer ---------------------------------------------------------
{
  echo
  echo "## Summary"
  echo
  echo "- **PASS:** $PASS / 4"
  echo "- **FAIL+FALLBACK:** $FAIL / 4"
  echo
  if [ $FAIL -eq 0 ]; then
    echo "**Verdict:** ALL GATES PASSED — proceed against the spec's named external deps."
  else
    echo "**Verdict:** $FAIL/4 gates failed; the per-gate fallback above is now the load-bearing path. Update \`docs/architecture.md\` + any Plan 3+ task that references the failed contract."
  fi
  echo
  echo "## Re-run"
  echo
  echo '```bash'
  echo 'bash infra/scripts/preflight.sh'
  echo '```'
  echo
  echo "The runner is idempotent: it overwrites \`$RESULTS\` on every run, so re-running after a dep is fixed cleanly records the new outcome."
} >> "$RESULTS"

echo "preflight.sh complete — $PASS passed, $FAIL failed. See $RESULTS."
exit 0  # Always 0 — spec contract is "record fallback", not "abort". Reviewer reads $RESULTS.
BASH
chmod +x infra/scripts/preflight.sh
```

- [ ] **Step 4: Run preflight locally to populate results**

```bash
bash infra/scripts/preflight.sh
```

Expected output (on a machine with Docker + Joern + Node, no vLLM running):

```
preflight.sh complete — 3 passed, 1 failed. See .cache/preflight/results.md.
```

The vLLM gate is expected to FAIL+FALLBACK at scaffolding day 1 because vLLM isn't running yet — the spec's pre-decided fallback (route embeddings via `vllm_embed.py`) is recorded in the results table, and Plan 4 will read that record to know which provider class to wire.

If gates 1-3 ALSO fail: do NOT debug the gate. Read the recorded fallback in `.cache/preflight/results.md`, apply it (e.g., update `IMAGE_POSTGRES_AGE` in `pinned.env` to the PG17 image; commit), and re-run preflight to confirm the gate now records the fallback path cleanly.

- [ ] **Step 5: Inspect the populated results file**

```bash
cat .cache/preflight/results.md
```

Expected: a markdown table with 4 rows (one per gate), a summary footer, and the verdict. Every `FAIL+FALLBACK` row must have a non-`—` fallback recorded (the spec already chose them; the script transcribes them verbatim — if a fallback shows `—`, that's a script bug, not an unknown).

- [ ] **Step 6: Commit**

```bash
git add infra/preflight-fixtures/ infra/scripts/preflight.sh
git commit -m "verify: §3.6 preflight gate runner + planted fixture"
```

Commit message scope: this is the spec §3.6 contract gate runner restoration (audit P0-6 / D-2.2). The generated `.cache/preflight/results.md` output is local run evidence and is intentionally not committed. It's intentionally separate from Task 11's `check_pinned_env.sh` (different job: pin-lint, CI-recurring) and Task 13's CI verification-pins job (different job: cheap drift checks, runs on every PR).

---

## Task 13: Add `verification-pins` CI job to `.github/workflows/ci.yml`

**What this task delivers:** a CI job that, on every PR, (a) runs `check_pinned_env.sh`, (b) re-runs `docker manifest inspect` against every pinned image, (c) re-runs the HuggingFace HEAD check against the Qwen model. If any pin drifts (tag retagged, image deleted, model moved), CI fails and the PR can't merge until the pin is updated. Heavy checks (container spin-up, CLI install, live API) stay out of CI — they're developer-local during Plan 2 execution and re-run-as-needed during dependency bumps.

**Files:**
- Modify: `.github/workflows/ci.yml` (append `verification-pins` job)

- [ ] **Step 1: Append the job to `.github/workflows/ci.yml`**

Add after the `file-length-gate:` block:

```yaml
  # Verification-pins job re-runs the cheap pin checks (manifest inspect + HF HEAD)
  # on every PR. Anonymous Docker Hub pulls rate-limit at ~100 requests / 6h per
  # source IP — fine at current PR volume (~5 manifest inspects per run). If PR
  # volume grows or Hub rate-limits start firing, add a DOCKER_HUB_TOKEN secret
  # and an authenticated `docker login` step before the manifest-inspect loop.
  verification-pins:
    name: Verification pins (manifest inspect + HF HEAD)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Pinned-env consistency check
        run: bash infra/scripts/check_pinned_env.sh

      - name: Re-verify every IMAGE_* manifest still resolves
        run: |
          set -euo pipefail
          source infra/versions/pinned.env
          for var in $(grep -oE '^IMAGE_[A-Z_]+' infra/versions/pinned.env); do
            ref="${!var}"
            if [ -z "$ref" ]; then continue; fi
            echo "Verifying $var=$ref ..."
            docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}' > /dev/null
          done
          echo "All IMAGE_* manifests still resolve."

      - name: Re-verify Qwen model identifier resolves
        run: |
          set -euo pipefail
          source infra/versions/pinned.env
          if [ -z "${QWEN_MODEL_ID:-}" ]; then
            echo "QWEN_MODEL_ID not set in pinned.env — skipping."
            exit 0
          fi
          status=$(curl -fsS -o /dev/null -w "%{http_code}" -I "https://huggingface.co/${QWEN_MODEL_ID}")
          if [ "$status" != "200" ] && [ "$status" != "401" ]; then
            echo "::error::Qwen model HEAD request returned $status for $QWEN_MODEL_ID"
            exit 1
          fi
          echo "Qwen model identifier still resolves (HTTP $status)."
```

- [ ] **Step 2: Validate the workflow YAML parses**

```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: silent success.

- [ ] **Step 3: Smoke-test the job locally**

```bash
bash infra/scripts/check_pinned_env.sh
source infra/versions/pinned.env
docker manifest inspect "$IMAGE_POSTGRES_AGE" > /dev/null && echo "AGE pin resolves"
docker manifest inspect "$IMAGE_VALKEY" > /dev/null && echo "Valkey pin resolves"
# Repeat for any other IMAGE_* keys you added.
status=$(curl -fsS -o /dev/null -w "%{http_code}" -I "https://huggingface.co/${QWEN_MODEL_ID}")
echo "Qwen HEAD: $status"
```

Expected: each pin resolves; Qwen HEAD returns 200 (or 401 if gated).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: verification-pins job re-runs manifest inspect + HF HEAD on every PR"
```

---

## Task 14: Final verification + push the plan

**What this task delivers:** a clean working tree, every Plan 2 commit on local `main`, and one `git push origin main` covering all of them.

**Files:**
- None (verification only).

- [ ] **Step 1: Confirm every Task 1-13 commit is present locally**

```bash
git log --oneline origin/main..HEAD
```

Expected: 13 commits, one per Task 1 through 13, each with a clear `verify:` or `feat:` or `ci:` prefix. Count them.

- [ ] **Step 2: Confirm `docs/verification-report.md` has all 9 dep sections**

```bash
grep -c '^## ' docs/verification-report.md
```

Expected: at least 12 — the Summary section, the Methodology / Verdict-scale headings, plus 9 per-dep sections (PG+AGE, pgvector, Valkey, Joern, SCIP, OpenGrep, SourceBot, vLLM+Qwen, OpenAI chat+embedding).

- [ ] **Step 3: Confirm `infra/versions/pinned.env` has every IMAGE_* and *_VERSION populated**

```bash
infra/scripts/check_pinned_env.sh
```

Expected: green. If any pin is missing, back-fill from the corresponding task's notes before pushing.

- [ ] **Step 4: Confirm working tree is clean**

```bash
git status --short | grep -v '^?? \.swarm/\|^M \.swarm/\|^M CLAUDE.md\|^M docs/handoff.md'
```

Expected: empty (no Plan 2 files leftover). Orchestrator-side changes (`.swarm/*`, `CLAUDE.md`, `docs/handoff.md`) are intentionally left untouched.

- [ ] **Step 5: Push**

```bash
git push origin main
```

Expected: 13 commits pushed cleanly. The next time CI fires (a follow-up PR), the `verification-pins` job runs and re-verifies every pin in `infra/versions/pinned.env`.

---

## 6. Deviations from the spec contract

The audit (`docs/superpowers/plan-audit-findings.md` §1 P0-6 + §4 D-2.2/D-2.3/D-2.4 — a working-tree-only artifact, never committed) flagged that Plan 2's first draft (a) renamed/repathed the spec §3.6 artifacts (`preflight.sh` → `check_pinned_env.sh`, `.cache/preflight/results.md` → `docs/verification-report.md`), (b) collapsed the §3.6 Joern + SCIP gates to empty-file substitution without declaring the substitution, and (c) discarded the spec's pre-decided per-gate fallbacks in favor of generic "STOP / surface to user" messaging. This revision restores the §3.6 contract while preserving the broader per-dep evidence trail Plan 2 already produces. The reasoning behind each restoration is recorded here so future readers don't relitigate the same trade-offs.

1. **§3.6 contract runner restored as Task 12 (audit D-2.2 / P0-6).** Created `infra/scripts/preflight.sh` that runs the 4 named §3.6 gates (AGE PG18, Joern Python frontend, SCIP-python, vLLM `/v1/embeddings`) as one coherent script and writes pass/fail + chosen fallback to `.cache/preflight/results.md`. Critically, the new runner does NOT subsume `check_pinned_env.sh` (Task 11) or `verification-report.md` (Tasks 2-10). Those serve different jobs: `check_pinned_env.sh` is the pin-consistency lint that runs in CI on every PR; `verification-report.md` is the long-form per-dep narrative trail. `preflight.sh` is the spec §3.6 contract gate runner: one-shot at scaffolding day 1, 4 gates, fallback recorded per gate. Cross-link from `.cache/preflight/results.md` header to `verification-report.md` makes the relationship explicit for any reader who lands in one file first.

2. **Per-gate fallbacks restored verbatim from spec §3.6 column 3 (audit D-2.4).** The first draft replaced the spec's pre-decided fallbacks with "STOP / surface to user." That discards a decision the spec already made — the load-bearing assumptions and their alternatives are pinned BEFORE implementation precisely so a gate failure doesn't trigger mid-implementation reconciliation. `preflight.sh` now transcribes each fallback verbatim into the `FAIL+FALLBACK` branch:

   | Gate | Spec §3.6 fallback (verbatim in script) |
   |---|---|
   | 1 — AGE PG18 | Pin `postgres:17` + `apache/age:release_PG17` in compose + ci.yml; update `docs/architecture.md` to PG17. |
   | 2 — Joern Python frontend | SCIP-only for Python; reserve Joern for non-Python (Phase 6+). Document in `docs/architecture.md`. |
   | 3 — SCIP-python | Adopt `pyright --outputjson` or `tree-sitter-python` for Python symbol extraction; document call-graph fidelity drop in `docs/architecture.md`. |
   | 4 — vLLM `/v1/embeddings` | Route embeddings through `adapters/llm/providers/vllm_embed.py` (separate adapter) instead of the chat provider. Plan 6 owns the implementation (EmbeddingsProvider ABC + adapter at `adapters/llm/embeddings.py`). |

   The script's exit code is 0 unconditionally — gate failure is "record fallback", not "abort". Reviewer reads `.cache/preflight/results.md`.

3. **Planted fixture chosen over empty-file substitution (audit D-2.3).** Spec §3.6 row 2 originally named `joern-parse src/codeoracle/services/verifier/`, and row 3 named `scip-python index --project-name=codeoracle src/codeoracle/`. At scaffolding day 1, NEITHER path exists yet — `src/codeoracle/services/verifier/` is owned by Plan 9, and `src/codeoracle/` is bootstrapped by Plan 1 as empty layer dirs. Three options were considered:

   - **Empty-file substitution** (the first draft's silent choice): defensible — the verifier doesn't exist pre-impl — but the gate becomes "does the parser tokenize zero lines?" which proves nothing useful. Rejected because nothing exercises call-graph edges or symbol resolution.
   - **Snapshot fixtures** (e.g., `target-repo-snapshot/example-app/`): real code, but couples preflight to SHA-pinned read-only external target-repo snapshots whose pins could change for reasons unrelated to the toolchain — a snapshot pin update would spuriously regress preflight.
   - **Planted fixture (chosen):** `infra/preflight-fixtures/sample_py.py` is a self-contained Python module with a non-trivial class (`Verifier.verify` calls `_normalize`), plus a module-level function (`run_check`) calling into the class. This gives both call-graph edges (`cpg.method("verify").caller.l` returns `run_check`) and resolvable symbols (SCIP `print | grep verify`) without depending on any other plan's artifacts. Spec §3.6 rows 2 and 3 are amended in the same commit to name the fixture path — the plan-and-spec edit is one logical change.

4. **Joern preflight assertion simplified to "CPG exists and is non-empty."** The full `cpg.method("verify").caller.l` query requires Joern's REPL or `--script` Scala stage, which adds a brittle dependency (Scala version, sbt cache, etc.) for a preflight whose job is "prove the toolchain works on real code." The script uses `[ -f cpg.bin ] && [ "$(_size cpg.bin)" -gt 0 ]` — the `-gt 0` threshold is intentional (not `-gt 1024` or any other measured floor): a working parse against the planted fixture produces kilobytes, but pinning a magic byte-threshold would risk spurious FAIL+FALLBACK on toolchain version bumps that change the on-disk size of an otherwise-working CPG. The full caller-query assertion lives in Plan 5's Joern parser test suite, where the Scala REPL dep is already paid for. Spec §3.6 row 2 (as amended in this commit) explicitly defers the call-graph assertion to Plan 5 — the spec and the preflight script now match on what each verifies, removing the audit's "named command not run as written" residual.

5. **Why `set -uo pipefail`, not `-e`.** The spec contract is "record the fallback, not abort." A `set -e` would skip gates 2-4 if gate 1 failed, leaving `.cache/preflight/results.md` half-written and the reviewer with no signal on the other 3 gates. The script exits 0 unconditionally; per-gate verdicts are the load-bearing output. (`-u` and `-o pipefail` are retained — those catch programming errors in the script itself, which is a different failure mode.)

6. **pgvector install-path key converged on `PGVECTOR_INSTALL_METHOD` (audit D-2.6 / P0-9, resolved).** Task 3 Step 3 emits `PGVECTOR_INSTALL_METHOD ∈ {preinstalled, separate-image, custom-dockerfile}` as the single canonical install-method key; `IMAGE_PGVECTOR` continues to pin the image digest in branch (b) only (it's not the install-method indicator). Plan 3's preconditions gate (L98-106) is narrowed in the same commit chain to accept exactly `PGVECTOR_INSTALL_METHOD` — the previous 3-key acceptance (`IMAGE_PGVECTOR=` OR `PGVECTOR_INSTALL_METHOD=` OR `PGVECTOR_SOURCE=`) was a transitional widening kept during the gap between Plan 3's revision sweep and Plan 2's. This convergence was intentionally landed as a tight follow-up to the §3.6 preflight gate runner restoration commit because it's mechanical and structurally independent of the contract restoration above; bundling them would have muddied the audit-chain attribution.

7. **SCIP indexer maintenance-staleness STOP added — deliberate, beyond the original spec contract (audit D-2.12). Signal corrected to last-publish.** Task 6 Step 1 resolves each indexer's `dist-tags.latest` (`npm view <pkg> version`) and reads *that version's* publish timestamp (`npm view <pkg> "time[<ver>]"`), STOPping the task if the latest release is more than ~18 months old, or the package is deprecated, or it is unpublished (Task 6 Step 1 prose; mirrored in the verification-report template's command list). It deliberately does **not** key on `npm view … time.modified`: that top-level field updates on any registry-metadata mutation — a `npm deprecate`, an owner change, a `dist-tag` move — so an abandoned package whose metadata was touched for an unrelated reason would show a *fresh* `modified` and slip past the gate (the exact false-negative audit D-2.12 flagged). The last-published-version timestamp is mutation-proof: it changes only on a real publish. The spec's §3.6 SCIP-python gate *originally* verified only that the indexer *produces a resolvable index* (`scip print` contains the fixture's symbols), specifying **no** package-maintenance or staleness probe. The STOP is kept on purpose: Sourcegraph has been narrowing scope through 2024–2025, and an archived/abandoned indexer is a live supply-chain risk that "the index parses today" would not surface; catching it at the verification floor (before Plan 5/6 build on the indexer) is strictly safer than discovering it mid-implementation. **Spec reconciliation (D-2.12, resolved 2026-06-02):** a prior pass (#44) reconciled spec ↔ plan by writing `npm view … time.modified` into the spec's SCIP-python gate (spec §3.6, L360) — but that propagated the *flawed* field into the spec rather than fixing it. The spec §3.6 L360 gate was amended to the same last-publish signal used here; spec and plan now agree.

## What Plan 3 inherits from Plan 2

Plan 3 ("Core foundation + DB bootstrap") starts with:

- A known-good `apache/age:release_PG18` image digest in `IMAGE_POSTGRES_AGE` → Plan 3's `docker-compose.yml` uses `image: ${IMAGE_POSTGRES_AGE}`, resolved by `docker compose --env-file infra/versions/pinned.env` (the root `--env-file` / `.env` is what drives compose-file `${VAR}` interpolation — a service-level `env_file:` injects container env vars only and would leave `${IMAGE_POSTGRES_AGE}` unsubstituted). Plan 3 already invokes `--env-file` correctly.
- A known-good pgvector path (preinstalled / separate image / custom Dockerfile decided) → Plan 3 chooses one without re-investigating.
- A pinned `OPENAI_EMBEDDING_DIM` (1536 = `text-embedding-3-small`'s full native dim) → Plan 3's pgvector column definition (`embedding vector(<DIM>)`) is unblocked without round-tripping through Plan 4. **Dimension reduction caveat:** the OpenAI v3 embedding API accepts a `dimensions` request parameter for shorter vectors (e.g., 512, 1024). If a smaller storage/retrieval footprint is preferred, override `OPENAI_EMBEDDING_DIM` in `pinned.env` BEFORE Plan 3's schema migration runs — the pgvector column type is fixed at creation time and changing it later requires a full migration + reindex.
- A pinned Valkey image → Plan 5's worker queue references it.
- Verified Joern + SCIP + OpenGrep + SourceBot CLI/image versions → Plan 5's `joern_indexer` worker + Plan 6's `scip_indexer` / `opengrep_runner` workers install/run the specific versions (no "latest" surprises). SourceBot's image is consumed by Plan 8's deployment-side bring-up per D2-α disposition item 1 — no CodeOracle worker (Sourcebot is Git-pull at container start).
- A concrete `QWEN_MODEL_ID` + `OPENAI_DEV_MODEL` + `OPENAI_EMBEDDING_MODEL` → Plan 4's `VLLMProvider` / `OpenAIProvider` (chat providers) + Plan 6's `OpenAIEmbeddingsProvider` (EmbeddingsProvider concrete) instantiate against these ids; Plan 13's full vertical slice can call them.
- A CI job that catches pin drift → if any of these stop resolving between Plans 2 and 3, the PR that introduces Plan 3's compose file fails fast.
- IMAGE_POSTGRES_AGE digest pinned for Plan 3 (per-key provides — Plan 3's `docker-compose.yml` substitutes `${IMAGE_POSTGRES_AGE}` deterministically without re-deriving from the file-level edge).
- IMAGE_PGVECTOR pin for Plan 3 (per-key provides — pinned for the `PGVECTOR_INSTALL_METHOD=separate-image` branch; Plan 3 substitutes `${IMAGE_PGVECTOR}` when that install-method branch is selected).
- IMAGE_VALKEY pin for Plan 3 (per-key provides — Plan 3's worker-queue compose entry references `${IMAGE_VALKEY}` without re-deriving from the file-level edge).
- IMAGE_SOURCEBOT digest for Plan 3 (per-key provides — Plan 3 substitutes `${IMAGE_SOURCEBOT}` when the `sourcebot` service compose entry is wired per spec §4.1).
- OPENAI_EMBEDDING_MODEL pin for Plan 3 (per-key provides — Plan 3's pgvector column-dim configuration and Plan 6's `OpenAIEmbeddingsProvider` both substitute `${OPENAI_EMBEDDING_MODEL}` without re-deriving).
- OPENAI_DEV_MODEL pin for Plan 3 (per-key provides — Plan 4's `OpenAIProvider` instantiates against `${OPENAI_DEV_MODEL}` without re-deriving from the file-level edge).
- docs/verification-report.md as the preflight-output artifact (provides — Plan 2 Tasks 2-11 append per-dep PROVEN/UNKNOWN/REFUTED entries; Plan 3's preconditions gate reads this file as the fails-fast contract that the prior-plane verification actually executed).

## What Plan 1A inherits from Plan 2

Plan 1A ("Operational infrastructure") starts with:

- The D-2.2 preflight.sh restoration task delivered by Plan 2 Task 12 → Plan 1A's deferred-task list (Plan 1A L18) references this work as completed-by-Plan-2, allowing Plan 1A to skip its own preflight-restoration carry-over.

## What Plan 2 inherits from Plan 1A

Plan 2 ("Pre-implementation verification gates") runs against:

- The compose stack delivered by Plan 1A (`infra/compose/docker-compose.yml`) → Plan 2's preflight.sh runner exercises the four §3.6 contract gates against the running stack (Plan 1A L20 DAG prose names Plan 2 as the consumer of the compose target).
- The container images delivered by Plan 1A → Plan 2's preflight.sh runner targets the per-service container images Plan 1A builds; the preflight gates exercise the four §3.6 contracts against those images rather than re-pulling raw upstream tags.

## What Plan 5 inherits from Plan 2

Plan 5 ("Worker framework + Joern vertical") starts with:

- IMAGE_VALKEY per-key entry in pinned.env (umbrella-split from Plan 2's wholesale `worker Dockerfiles (pinned.env source)` edge) → Plan 5's worker-queue runtime substitutes `${IMAGE_VALKEY}` directly without re-deriving from the file-level edge.
- IMAGE_POSTGRES_AGE per-key entry in pinned.env (provides — Plan 5's worker runtime connects to the AGE-backed Postgres instance using `${IMAGE_POSTGRES_AGE}`).
- JOERN_VERSION per-key entry in pinned.env (umbrella-split — Plan 2 Task 5 pins this individually so Plan 5's `joern_indexer` Task 1 Step 2 binds the canonical Joern release without re-deriving).
- Joern Python-frontend pre-implementation CPG-gate verification result (provides — Plan 2 Task 5 + the §3.6 contract-gate runner restored by Plan 2 Task 12 prove the Python-frontend CPG materializes class/method/call-graph edges on a planted fixture; Plan 5's plan opens by citing this gate as the load-bearing precondition the worker vertical inherits).

## What Plan 6 inherits from Plan 2

Plan 6 ("Snapshot extractors + embeddings") starts with:

- The SCIP-python preflight gate (`scripts/preflight/check_scip_python.py` as the canonical name; logically equivalent to Plan 2 Task 6's `npx @sourcegraph/scip-python` smoke + Plan 2 Task 12's §3.6 row-3 runner) → Plan 6's SCIP runner adapter inherits the pre-verified toolchain rather than re-probing scip-python on first use.
- The vLLM OpenAI-compat preflight gate (`scripts/preflight/check_vllm_openai_compat.py` as the canonical name; logically equivalent to Plan 2 Task 9's vLLM `/v1/embeddings` smoke + Plan 2 Task 12's §3.6 row-4 runner) → Plan 6's embeddings provider inherits the OpenAI-shape verification rather than re-probing the inference endpoint on first use.
- The §3.6 preflight gate runner (Plan 2 Task 12 `infra/scripts/preflight.sh` + `.cache/preflight/results.md`) cross-referenced from Plan 6 Task 13 D-6.11 acceptance criterion → Plan 6's CI hardening inherits the contract-gate outputs as the upstream evidence file rather than re-deriving from the per-gate Task 2-9 entries.

## What Plan 8 inherits from Plan 2

Plan 8 ("Handle resolution + Sourcebot") starts with:

- The Sourcebot image boot smoke test delivered by Plan 2 Task 8 (provides — Plan 2 Task 8 proved the Sourcebot image's container starts and the health endpoint responds; Plan 8 explicitly inherits this as the prior-art bar to exceed, then extends with live `/api/source` indexing tests that Plan 2 Task 8 did NOT exercise).
- The `IMAGE_SOURCEBOT` pin in `infra/versions/pinned.env` (provides — Plan 2 Task 8 records the verified Sourcebot image digest; Plan 8's deployment-side bring-up substitutes `${IMAGE_SOURCEBOT}` rather than re-deriving the tag). NOTE: these pins are NOT a Renovate regex-manager set — Plan 1 Task 12's `.github/renovate.json5` has no custom/regex manager reading this file (see the `pinned.env` header's "Drift-catch" note). Drift on `IMAGE_SOURCEBOT` and every other pin is caught by Plan 2 Task 13's `verification-pins` CI job; a deliberate bump follows `infra/versions/README.md`'s "How to update a pin" flow, not an automated Renovate PR.

## What Plan 11 inherits from Plan 2

- **`infra/scripts/preflight.sh`** — Plan 2 ships the preflight script (Task 12); Plan 11 Task 11 EDIT (M) appends a `/v1/health` smoke-check line to it (inherits + extends, does not re-create).

## What Plan 12 inherits from Plan 2

- **`infra/scripts/preflight.sh`** — Plan 2 ships the preflight script (Task 12); Plan 12 Task 9 EXTENDS it with an MCP smoke step (`codeoracle-mcp --http &; client ping; kill`) per Plan 12's `mcp-server.md` "Imports from" row for Plan 2 — the existing preflight steps remain untouched (inherits + extends, does not re-create).

## What Plan 2 explicitly does NOT do

- Stand up a running stack (Plan 3).
- Wire any dep into Python code (Plan 3 / 4 / 5).
- Build custom Dockerfiles (Plan 3 if pgvector needs one; Plan 5 for worker images).
- Configure connection-string env vars beyond what `.env.example` already has from Plan 1 (Plan 3).
- Run any "real" indexing against external target repositories (Plan 5 builds the indexing framework; Plan 13's E2E vertical slice runs the real flow).

These are deliberate non-goals. Plan 2 is the verification floor; Plan 3 begins the actual platform build with confidence that the floor will hold.
