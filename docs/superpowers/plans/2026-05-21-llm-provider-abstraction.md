# LLM Provider Abstraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `src/codeoracle/adapters/llm/` (interface, factory, config, fingerprint helper, five concrete providers) + `src/codeoracle/services/context_budget.py` (ContextBudgetService, lives under services/ per `strict-layer-purity`) + structural ruff enforcement of "no direct LLM SDK imports outside providers/" + a deterministic MockLLMProvider + a label-gated live-LLM CI workflow with one smoke test, so Plans 5–7 inherit a provider-agnostic LLM facade.

**Architecture:** The `LLMProvider` ABC is the single seam every other layer talks to. The factory selects a concrete provider from `LLMConfig.provider` (env-driven, OpenAI default per memory `project-dev-llm-provider-openai`); each provider constructs its async client in `__init__` and reuses it across calls; every provider passes outgoing messages through `ContextBudgetService.check()` before sending. The MockLLMProvider keys responses by `request_fingerprint(messages, tools, *, model, temperature, max_tokens, structured_output)` — the full spec §5.7 "key parameters" set — so tests are deterministic, cosmetic prompt edits don't shatter the suite, AND a request with different decoding params (model, temperature, max_tokens) or a different structured-output target resolves to a different fixture. Structural enforcement (ruff `flake8-tidy-imports.banned-api`) prevents `import openai|anthropic|litellm|ollama` outside `src/codeoracle/adapters/llm/providers/*.py` — CI fails on the offending PR; no human vigilance required.

**Tech Stack:** Python 3.14, OpenAI Python SDK (async + a sync client used only for Anthropic's count-tokens API), Anthropic Python SDK (async for `chat`, sync for `count_tokens`), `httpx` (vLLM + Ollama direct HTTP, both honor the OpenAI-compatible `/v1/chat/completions` shape), `tiktoken` (exact OpenAI token counting), `tokenizers` (exact local vLLM/Ollama token counting via the served model's Hugging Face tokenizer — the standalone Rust lib, not the heavy `transformers` package), `respx` (HTTP mocking for unit tests of httpx-based providers), `PyYAML` (MockLLMProvider script loading), `pytest-asyncio` ≥ 0.23 (`asyncio_mode = "auto"` from Plan 3 Task 1).

**Spec source:** `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` §5.1–§5.7 (LLM provider abstraction), §3.2 (file layout under `adapters/llm/`), §3.3 (layer purity — adapters wrap external systems, services hold domain logic), §3.6 (pre-implementation gates — `OPENAI_API_KEY` works, embeddings endpoint shape — embeddings deferred to Plan 5).

**Inherits from Plan 3:**
- `src/codeoracle/core/config.py` — `BaseSettings` pattern (Plan 4's `LLMConfig` inherits from `BaseSettings` the same way).
- `src/codeoracle/core/logging.py` — the redaction processor strips top-level `api_key`/`token`/`secret`/`password` event keys as a second layer. Every provider `api_key` is typed `SecretStr` (primary defense): repr-masking stops the secret leaking when a config is nested inside a log payload, exception, `model_dump()`, or OTel span attribute — places the name-keyed processor never sees. Matches Plan 8's `SourcebotConfig.api_key`.
- `src/codeoracle/core/exceptions.py` — `CodeOracleError` base. `ContextBudgetExceeded` and `LLMProviderError` (a new subclass introduced in Plan 4 Task 4) inherit from it.
- `src/codeoracle/models/llm.py` — `ChatMessage`, `ChatResponse`, `TokenUsage`, `ToolCall`, `ToolDefinition`. **Plan 4 Task 2 extends this module** by adding `ChatMessage.template_id: str | None = None` — required by the fingerprint algorithm (§5.7 line 734: "stable hash of (system_prompt_template_id, user_message_template_id, key parameters) — not raw bytes").
- `infra/versions/pinned.env` (Plan 2) — `OPENAI_DEV_MODEL` (used as `OpenAIProviderConfig.model` default), `OPENAI_API_KEY` (live-LLM gate precondition). Plan 4 fails fast if `OPENAI_DEV_MODEL` is missing.

**Hands off to later plans:**
- Plan 5 (extractors + workers): inherits `LLMProvider` ABC + factory. `embed_indexer` does NOT use `LLMProvider` — it uses a separate `EmbeddingsProvider` ABC (Plan 5 introduces this; spec §3.6 line 361 anticipates a `providers/vllm_embed.py` adapter if embeddings shape diverges from chat).
- Plan 6 (domain services): inherits the full facade. `TrustMatrixService` + `VerifierService` are pure-domain — they DO NOT call LLM. `HandleResolverService` MAY use LLM for graph-query construction; if so, it receives an `LLMProvider` via constructor injection.
- Plans 10/11/12 (agent + API + MCP processes): inherit the facade + `ContextBudgetService` for the LLM calls each process makes; Plan 13 (E2E vertical slice) extends the live-LLM workflow with the 9 vertical-slice integration tests (the smoke test landed in Plan 4 is the first one).

**Deferred (NOT in Plan 4 — explicit non-goals):**
- **Streaming** (`async def stream(...)`). The ABC's docstring notes this is a future extension point but commits no shape. Adding it later doesn't break callers because they only consume `chat()`.
- **Embeddings provider** — separate contract, separate adapter (Plan 5).
- **Live-LLM gate provider matrix** (Anthropic, vLLM in CI). Plan 13 decides scope once the 9 integration tests exist.
- **Fixture-generation dev tool** (records live responses into YAML). Comes when fixtures need a bulk refresh, not now.

---

## Open contract — what Plan 4 must hand to Plan 5+

| Surface | Contract |
|---|---|
| `codeoracle.adapters.llm.interfaces.LLMProvider` | ABC with `async chat`, `count_tokens`, `model_name`, `max_context_tokens`, and `async aclose` (default no-op; concrete providers with persistent HTTP/SDK clients override — see Plan 10 Task 11 lifespan shutdown). |
| `codeoracle.adapters.llm.factory.LLMProviderFactory.create()` | Returns the RAW `LLMProvider` for `LLMConfig.provider`. Wrapped by `codeoracle.services.llm.build_budgeted_provider` (the blessed construction path) — composition roots never call the factory directly; a bare product is unbounded. |
| `codeoracle.adapters.llm.config.LLMConfig` | A `BaseSettings` subclass. `provider="openai"` by default. Per-provider sub-configs (`openai: OpenAIProviderConfig \| None`, etc.). Cross-validator ensures the selected sub-config is non-None. |
| `codeoracle.services.context_budget.ContextBudgetService` | Single-method service: `check(messages, token_counter) -> int`. Raises `ContextBudgetExceeded`. Applied by the `BudgetedLLMProvider` decorator (`services/llm/`) before send; raw providers never reference it (NFR D7a-1). |
| `codeoracle.services.llm.BudgetedLLMProvider` / `build_budgeted_provider(config, budget)` | Service-layer decorator wrapping a raw `LLMProvider` to enforce `ContextBudgetService.check()` before each `chat()`. The helper is the single blessed construction path — wraps `LLMProviderFactory(config).create()` so no composition root holds an unbounded provider. Keeps `adapters/llm/` free of any upward import into `services/` (NFR D7a-1). |
| `codeoracle.adapters.llm.fingerprint.request_fingerprint(messages, tools, *, model, temperature=None, max_tokens=None, structured_output=None) -> str` | Deterministic SHA256-based hash over messages + tools + spec §5.7 "key parameters" (`model` is required; `temperature` / `max_tokens` / `structured_output` are optional and default to None so callers can opt in as call-time params get exposed). Used by MockLLMProvider for lookup and (future) by the fixture-recording tool. |
| ruff banned-api enforcement | `import openai\|anthropic\|litellm\|ollama` outside `src/codeoracle/adapters/llm/providers/*.py` fails CI. |
| `.github/workflows/test-live-llm.yml` | PR-label-triggered (`run-live-llm`) workflow that runs `pytest -m live_llm` against real OpenAI. Plan 4 lands ONE smoke test under this marker; Plan 13 adds the 9 slice tests. |

---

## Pre-implementation sanity checks

Before any task: confirm Plan 3 is checked-in, the application schema migrated end-to-end, and pinned.env exposes the required keys.

```bash
# Plan 3 artifacts must exist
test -f src/codeoracle/core/config.py || { echo "Plan 3 not applied"; exit 1; }
test -f src/codeoracle/core/exceptions.py || { echo "Plan 3 not applied"; exit 1; }
test -f src/codeoracle/models/llm.py || { echo "Plan 3 not applied"; exit 1; }

# Plan 2 pinned.env must expose the LLM vars
source infra/versions/pinned.env
for v in OPENAI_DEV_MODEL OPENAI_API_KEY; do
  if [ -z "${!v:-}" ] && [ "$v" != "OPENAI_API_KEY" ]; then
    # OPENAI_API_KEY is set by the developer locally / CI secret, not in pinned.env.
    echo "ERROR: pinned.env missing $v"; exit 1
  fi
done
echo "OPENAI_DEV_MODEL = $OPENAI_DEV_MODEL"

# Make sure ruff is installed (from Plan 1 Task)
uv run ruff --version
```

Expected: `OPENAI_DEV_MODEL = gpt-4o-mini` (or whatever Plan 2 pinned) and ruff version printed.

---

## Files

**Created:**
- `src/codeoracle/services/__init__.py` (empty package init)
- `src/codeoracle/services/context_budget.py`
- `src/codeoracle/adapters/__init__.py` (if not created by Plan 3)
- `src/codeoracle/adapters/llm/__init__.py`
- `src/codeoracle/adapters/llm/interfaces.py`
- `src/codeoracle/adapters/llm/fingerprint.py`
- `src/codeoracle/adapters/llm/tokenizer_loader.py`
- `src/codeoracle/adapters/llm/config.py`
- `src/codeoracle/adapters/llm/factory.py`
- `src/codeoracle/adapters/llm/providers/__init__.py`
- `src/codeoracle/adapters/llm/providers/mock.py`
- `src/codeoracle/adapters/llm/providers/openai.py`
- `src/codeoracle/adapters/llm/providers/anthropic.py`
- `src/codeoracle/adapters/llm/providers/vllm.py`
- `src/codeoracle/adapters/llm/providers/ollama.py`
- `tests/unit/services/test_context_budget.py`
- `tests/unit/adapters/llm/test_fingerprint.py`
- `tests/unit/adapters/llm/test_tokenizer_loader.py`
- `tests/unit/adapters/llm/test_config.py`
- `tests/unit/adapters/llm/test_factory.py`
- `tests/unit/adapters/llm/providers/test_mock.py`
- `tests/unit/adapters/llm/providers/test_openai.py`
- `tests/unit/adapters/llm/providers/test_anthropic.py`
- `tests/unit/adapters/llm/providers/test_vllm.py`
- `tests/unit/adapters/llm/providers/test_ollama.py`
- `tests/unit/test_banned_api_enforcement.py` (CI-side test)
- `tests/integration/llm/__init__.py`
- `tests/integration/llm/test_openai_live_roundtrip.py` (the live-LLM smoke test)
- `tests/fixtures/llm_scripts/test_explain_existing_symbol.yaml` (one canonical example — Plan 13 adds more)
- `.github/workflows/test-live-llm.yml`

**Modified:**
- `pyproject.toml` — Plan 4 runtime + dev dependencies + ruff banned-api config + pytest markers (`live_llm`)
- `src/codeoracle/models/llm.py` — add `template_id: str | None = None` to `ChatMessage`
- `.env.example` — un-comment the LLM placeholder lines, document the env shape

---

## Task 1: Add Plan 4 runtime + dev dependencies + ruff banned-api + pytest marker

**What this task delivers:** the package additions Plan 4 needs (`openai`, `anthropic`, `httpx` if not already present from Plan 3, `tiktoken`, `tokenizers`, `PyYAML` at runtime; `respx`, `types-PyYAML` for dev), *verification* that Plan 1 Task 4's ruff `flake8-tidy-imports.banned-api` rule + `providers/*.py` glob per-file-ignore are intact (Plan 4 authors no ruff config — Plan 1 owns both), and the `live_llm` pytest marker so the live-LLM gate has a clean filter target.

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Verify ruff version supports `flake8-tidy-imports.banned-api`**

```bash
uv run ruff --version
# Need ruff >= 0.5.0 (banned-api in TID251). Plan 1 pinned a version; verify.
```

Expected: a version >= 0.5.0. If lower, this is a Plan 1 regression — STOP and amend Plan 1's pin before continuing.

- [ ] **Step 2: Add runtime dependencies via `uv add`**

```bash
uv add openai anthropic httpx tiktoken tokenizers PyYAML
```

`tokenizers` (the standalone Hugging Face Rust tokenizer, NOT the heavy
`transformers` package) backs vLLM's and Ollama's exact local `count_tokens`:
`Tokenizer.from_pretrained("<repo-id>")` fetches `tokenizer.json` once (then
disk-cached) and encodes with zero per-call network round-trip. If a model
repo ships no `tokenizer.json` or the host is offline at construction, the
provider logs a WARNING and falls back to a labeled `len//4` heuristic.

- [ ] **Step 3: Add dev dependencies via `uv add --dev`**

```bash
uv add --dev respx types-PyYAML
```

- [ ] **Step 4: Verify the `flake8-tidy-imports.banned-api` block is already present (Plan 1 Task 4 owns it)**

Plan 1 Task 4 Step 2 (commit `1d384b2`) already wrote the canonical `[tool.ruff.lint.flake8-tidy-imports.banned-api]` block + the single `providers/*.py` glob per-file-ignore. Plan 4 must NOT append a second block of the same name — TOML rejects duplicate table headers, and even if it didn't, two competing `.msg` strings for the same module would muddy the violation message. Plan 4's job here is to verify the block is intact:

```bash
# Required entries exist (presence check — message-agnostic to avoid
# coupling Plan 4 to Plan 1's exact wording)
for module in openai anthropic litellm ollama; do
  grep -qE "^\"${module}\".msg\s*=" pyproject.toml || {
    echo "ERROR: banned-api entry for '${module}' missing — Plan 1 Task 4 regression."
    exit 1
  }
done

# The block header itself
grep -qE '^\[tool\.ruff\.lint\.flake8-tidy-imports\.banned-api\]' pyproject.toml || {
  echo "ERROR: [tool.ruff.lint.flake8-tidy-imports.banned-api] block missing — Plan 1 Task 4 regression."
  exit 1
}

echo "OK — Plan 1's banned-api block intact."
```

If any check fails, that's a Plan 1 regression — STOP, fix Plan 1's block, do NOT add a competing block here. Plan 4 contributes zero new TOML entries to the banned-api table; the audit-found "second table header append" defect (D-4.6 / P0-10) is closed by this verification.

- [ ] **Step 5: Verify the per-file-ignore glob covers all providers**

Plan 1 Task 4 Step 3 wrote a single `providers/*.py` glob per-file-ignore (per spec §5.6 / §7.5: `"src/codeoracle/adapters/llm/providers/*.py" = ["TID251"]`) — it exempts every file under `providers/` from the banned-api boundary by construction, so no provider needs a hand-enumerated entry. Plan 4 Task 7 adds `mock.py`, which is covered automatically by the glob (it imports no banned SDK today, so it has nothing to ignore, but the glob would cover it if it ever did). So Plan 4 adds no per-file-ignore here. Verify Plan 1's glob is present:

```bash
grep -qE "\"src/codeoracle/adapters/llm/providers/\*\.py\".*\[\"TID251\"\]" pyproject.toml || {
  echo "ERROR: providers/*.py per-file-ignore glob missing — Plan 1 Task 4 Step 3 regression."
  exit 1
}
echo "OK — providers/*.py per-file-ignore glob intact (covers every provider, incl. mock.py, by construction)."
```

The `providers/*.py` glob is preferred over a hand-enumerated per-provider list because (a) the list rots — every provider a later plan adds (`mock.py`, `litellm.py`, embedding providers) would need a new line or silently lose its exemption, and (b) the glob covers `mock.py` automatically without a config touch. The catalogue of which providers exist is the `providers/` directory listing itself, not a duplicated config block.

- [ ] **Step 6: Add `live_llm` to `[tool.pytest.ini_options].markers`**

Locate the `markers = [...]` list in `pyproject.toml` (added in Plan 3 Task 1 Step 4) and add:

```toml
markers = [
    "integration: requires a running docker-compose stack (postgres + valkey)",
    "live_llm: requires OPENAI_API_KEY + network; runs only in label-gated CI workflow",
]
```

- [ ] **Step 7: Verify ruff still accepts the config**

```bash
uv run ruff check src/ tests/
```

Expected: no errors (or only pre-existing ones; Plan 4 hasn't added code yet that would trigger banned-api).

- [ ] **Step 8: Verify pytest collects markers without warnings**

```bash
uv run pytest --collect-only -m live_llm tests/ 2>&1 | grep -i 'unknown.*marker\|warning' || echo "OK — no unknown-marker warnings"
```

Expected: prints `OK — no unknown-marker warnings`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: Plan 4 Task 1 — LLM deps + ruff banned-api + live_llm marker"
```

---

## Task 2: Extend `src/codeoracle/models/llm.py` with `ChatMessage.template_id`

**What this task delivers:** a backward-compatible extension to Plan 3's `ChatMessage` model adding the optional `template_id` field that the fingerprint algorithm uses. This is the only Plan 3 model change Plan 4 makes; documented explicitly because Plan 3 is committed and inheritance otherwise reads "Plan 4 only adds, never modifies."

**Why a Plan 3 model needs Plan 4 changes:** spec §5.7 line 734 requires the request fingerprint to hash `(system_prompt_template_id, user_message_template_id, key parameters)` — not raw content — so cosmetic prompt edits don't shatter the test suite. Without a `template_id` on `ChatMessage`, the fingerprint must hash raw content. The Plan 3 model didn't include this because Plan 3 was focused on persistence types and the fingerprint algorithm is Plan 4's responsibility.

**Files:**
- Modify: `src/codeoracle/models/llm.py`
- Modify: `tests/unit/models/test_llm.py` (extend Plan 3 test)

- [ ] **Step 1: Locate the `ChatMessage` class in `src/codeoracle/models/llm.py`**

The Plan 3 shape (line numbers may drift):

```python
class ChatMessage(BaseModel):
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
```

- [ ] **Step 2: Add the `template_id` field**

Modify `ChatMessage` to add `template_id`:

```python
class ChatMessage(BaseModel):
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    template_id: str | None = None
    """Optional stable identifier for the prompt template that produced this
    message. Used by `codeoracle.adapters.llm.fingerprint.request_fingerprint`
    so MockLLMProvider lookup survives cosmetic edits to template text.
    None for ad-hoc messages (e.g., a user's free-text query); callers
    invoking a templated message MUST set this."""
```

- [ ] **Step 3: Write the new test**

In `tests/unit/models/test_llm.py`, append:

```python
def test_chat_message_template_id_optional_and_default_none():
    """Plan 4 added template_id. Plan 3 callers must still work without setting it."""
    msg = ChatMessage(role="user", content="hello")
    assert msg.template_id is None


def test_chat_message_template_id_round_trips():
    msg = ChatMessage(role="system", content="...", template_id="intent_parse_v1")
    assert msg.template_id == "intent_parse_v1"
    dumped = msg.model_dump()
    rehydrated = ChatMessage(**dumped)
    assert rehydrated.template_id == "intent_parse_v1"
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest tests/unit/models/test_llm.py -v
```

Expected: PASS, including the two new tests and all Plan 3 tests still passing.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/models/llm.py tests/unit/models/test_llm.py
git commit -m "feat: Plan 4 Task 2 — extend ChatMessage with template_id field"
```

---

## Task 3: `src/codeoracle/services/context_budget.py` — ContextBudgetService + ContextBudgetExceeded

**What this task delivers:** the single-method `ContextBudgetService.check()` that every LLM provider calls before sending. Lives under `services/` (NOT `adapters/`) per memory `strict-layer-purity` — context budget is a domain rule (the platform refuses to call an LLM with > N tokens), not an LLM-specific I/O wrapper.

**Files:**
- Create: `src/codeoracle/services/__init__.py` (empty)
- Create: `src/codeoracle/services/context_budget.py`
- Create: `tests/unit/services/__init__.py` (empty)
- Create: `tests/unit/services/test_context_budget.py`

- [ ] **Step 1: Write `src/codeoracle/services/__init__.py` (empty)**

```bash
touch src/codeoracle/services/__init__.py
```

- [ ] **Step 2: Write `src/codeoracle/services/context_budget.py`**

```python
"""Context budget enforcement — refuses LLM calls that exceed the platform-wide
token cap. Lives in services/ (not adapters/) because the cap is a domain
rule: the platform's safety contract says no single LLM call exceeds N tokens.

Every concrete LLMProvider implementation (codeoracle.adapters.llm.providers.*)
calls `ContextBudgetService.check(messages, self.count_tokens)` before sending.
Refusal happens upstream of the SDK call.
"""

from collections.abc import Callable

from codeoracle.core.exceptions import CodeOracleError
from codeoracle.models.llm import ChatMessage


class ContextBudgetExceeded(CodeOracleError):
    """Raised when a prepared message list exceeds the configured token cap.

    Subclass of CodeOracleError so all platform exceptions share a single ancestor.
    """


class ContextBudgetService:
    """Enforces the platform-wide token cap on outgoing LLM requests.

    Default 24_000 tokens — the spec §3.6 settled this as the safe operating
    window for the production target (Qwen3.6 served via vLLM). Construction
    point is the composition root (api/deps.py for the API process, workers/
    _base/wiring.py for worker processes); each takes its own instance.
    """

    def __init__(self, max_tokens: int = 24_000) -> None:
        if max_tokens <= 0:
            raise ValueError(
                f"ContextBudgetService.max_tokens must be > 0, got {max_tokens}"
            )
        self._max_tokens = max_tokens

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def check(
        self,
        messages: list[ChatMessage],
        token_counter: Callable[[str], int],
    ) -> int:
        """Sum token-counts of `messages.content` using `token_counter`.

        Returns the total. Raises ContextBudgetExceeded if total > max_tokens.

        `token_counter` is the provider's `count_tokens` method (passed in
        rather than imported because each provider counts differently —
        OpenAI uses tiktoken, vLLM approximates, etc.). This keeps
        ContextBudgetService provider-agnostic.
        """
        total = sum(token_counter(m.content) for m in messages)
        if total > self._max_tokens:
            raise ContextBudgetExceeded(
                f"prepared request totals {total} tokens, "
                f"exceeds max {self._max_tokens}"
            )
        return total
```

- [ ] **Step 3: Write `tests/unit/services/__init__.py` (empty)**

```bash
touch tests/unit/services/__init__.py
```

- [ ] **Step 4: Write `tests/unit/services/test_context_budget.py`**

```python
"""Plan 4 Task 3: ContextBudgetService unit tests."""

import pytest

from codeoracle.core.exceptions import CodeOracleError
from codeoracle.models.llm import ChatMessage
from codeoracle.services.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetService,
)


def test_max_tokens_must_be_positive():
    with pytest.raises(ValueError):
        ContextBudgetService(max_tokens=0)
    with pytest.raises(ValueError):
        ContextBudgetService(max_tokens=-1)


def test_check_under_budget_returns_total():
    svc = ContextBudgetService(max_tokens=100)
    msgs = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
    ]
    # token_counter: len(content) — keep it deterministic and easy to assert
    total = svc.check(msgs, token_counter=lambda s: len(s))
    assert total == len("hello") + len("hi")


def test_check_at_budget_returns_total_no_raise():
    svc = ContextBudgetService(max_tokens=5)
    msgs = [ChatMessage(role="user", content="hello")]  # len == 5
    assert svc.check(msgs, token_counter=lambda s: len(s)) == 5


def test_check_over_budget_raises_context_budget_exceeded():
    svc = ContextBudgetService(max_tokens=4)
    msgs = [ChatMessage(role="user", content="hello")]
    with pytest.raises(ContextBudgetExceeded) as exc_info:
        svc.check(msgs, token_counter=lambda s: len(s))
    assert "5 tokens" in str(exc_info.value)
    assert "max 4" in str(exc_info.value)


def test_context_budget_exceeded_is_oracle_error():
    """All platform exceptions must inherit CodeOracleError so catch-all handlers work."""
    assert issubclass(ContextBudgetExceeded, CodeOracleError)


def test_max_tokens_property_exposes_configured_value():
    svc = ContextBudgetService(max_tokens=24_000)
    assert svc.max_tokens == 24_000


def test_empty_messages_returns_zero():
    svc = ContextBudgetService(max_tokens=100)
    assert svc.check([], token_counter=lambda s: len(s)) == 0
```

- [ ] **Step 5: Run the test**

```bash
uv run pytest tests/unit/services/test_context_budget.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Run lint + type**

```bash
uv run ruff check src/codeoracle/services/ tests/unit/services/
uv run ty check src/codeoracle/services/
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/services/__init__.py src/codeoracle/services/context_budget.py \
        tests/unit/services/__init__.py tests/unit/services/test_context_budget.py
git commit -m "feat: Plan 4 Task 3 — ContextBudgetService (domain-rule layer)"
```

---

## Task 4: `src/codeoracle/adapters/llm/interfaces.py` — LLMProvider ABC + LLMProviderError

**What this task delivers:** the ABC that every concrete provider implements + a base exception (`LLMProviderError`, inherits `CodeOracleError`) that concrete providers raise on SDK errors. The ABC has no concrete dependencies; this task creates the package init files and the interface only.

**Files:**
- Create: `src/codeoracle/adapters/__init__.py` (empty, if Plan 3 didn't)
- Create: `src/codeoracle/adapters/llm/__init__.py` (empty)
- Create: `src/codeoracle/adapters/llm/interfaces.py`
- Create: `tests/unit/adapters/__init__.py` (empty)
- Create: `tests/unit/adapters/llm/__init__.py` (empty)

- [ ] **Step 1: Ensure `src/codeoracle/adapters/__init__.py` exists**

```bash
test -f src/codeoracle/adapters/__init__.py || touch src/codeoracle/adapters/__init__.py
touch src/codeoracle/adapters/llm/__init__.py
mkdir -p tests/unit/adapters/llm
touch tests/unit/adapters/__init__.py tests/unit/adapters/llm/__init__.py
```

- [ ] **Step 2: Add `LLMProviderError` to `src/codeoracle/core/exceptions.py`**

Open `src/codeoracle/core/exceptions.py` (Plan 3 Task 2) and append:

```python
class LLMProviderError(CodeOracleError):
    """Raised by concrete LLMProvider implementations on SDK / transport
    failures (auth, rate-limit, malformed response, network). Concrete
    providers wrap SDK-specific exceptions in this class so callers can
    catch one exception type regardless of which provider is configured.
    """
```

(Place after the existing exception classes; alphabetical with the rest is fine.)

- [ ] **Step 3: Write `src/codeoracle/adapters/llm/interfaces.py`**

```python
"""LLMProvider ABC — the single seam all platform code uses to call an LLM.

No concrete provider SDK is imported here. Each concrete provider lives
under providers/ and imports its SDK locally (the only filesystem location
where direct SDK imports are allowed, enforced by ruff TID251 per-file-ignore).

`async def chat()` accepts a prepared ChatMessage list + optional tools +
optional structured_output type, and returns a ChatResponse. The provider
implementations are responsible for:
  1. Calling ContextBudgetService.check(messages, self.count_tokens) BEFORE send.
  2. Translating to provider-native request shape, calling the async client.
  3. Translating provider response back to the platform's ChatResponse type.
  4. Wrapping SDK exceptions in LLMProviderError.

Streaming is intentionally NOT in this ABC. A future plan may add
`async def stream(...) -> AsyncIterator[ChatResponseChunk]` as a separate
abstract method; that's an additive change that doesn't break existing
callers.
"""

from abc import ABC, abstractmethod

from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition


class LLMProvider(ABC):
    """Provider-agnostic contract for sending a chat turn to an LLM.

    Lifecycle note: concrete providers (OpenAI / Anthropic / vLLM / Ollama)
    construct async HTTP clients in ``__init__`` and reuse them across calls.
    The ABC ships ``async def aclose(self) -> None`` with a default no-op
    body (below); providers with persistent connections override it to drain
    their client (see each provider's ``aclose()`` docstring for the specific
    drain call). Plan 10's agent process (Task 11) registers ``aclose()`` in
    its FastAPI lifespan shutdown so HTTP/SDK connections drain cleanly
    before process exit; Plan 11 + Plan 12 inherit the pattern when written.
    Tests that need manual drain semantics can call ``await provider.aclose()``
    directly.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        """Send `messages` (and optional tool definitions / structured-output
        target type) to the underlying LLM. Returns a fully populated
        ChatResponse (content + usage + finish_reason + tool_calls).

        Implementations MUST wrap SDK exceptions in LLMProviderError. Budget
        enforcement is NOT a provider concern: the BudgetedLLMProvider decorator
        (services/llm/) calls ContextBudgetService.check() around this method, so
        a raw provider MUST NOT import or reference the budget (NFR D7a-1).
        """

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Synchronously return the token count of `text` using the EXACT
        tokenizer for this provider's model wherever one is available.

        Used by ContextBudgetService.check() (driven by the BudgetedLLMProvider
        decorator). Implementations:
          - OpenAI: tiktoken with the model-correct encoding
            (encoding_for_model; o200k_base fallback for unknown 4o-class ids).
          - Anthropic: the real count-tokens API via a synchronous client,
            memoized by content hash (no local Anthropic tokenizer exists).
          - vLLM / Ollama: the served model's Hugging Face tokenizer loaded
            locally (tokenizer_loader), encoding with zero per-call round-trip.
          - Mock: deterministic stub (len(text) // 4).
        A labeled `len // 4` heuristic is the LOGGED last-resort fallback only
        when the exact path is unavailable (count API errors; HF tokenizer
        can't load — e.g. an Ollama tag isn't a Hub repo id). Never silent.
        """

    @abstractmethod
    def model_name(self) -> str:
        """The configured model identifier (e.g., 'gpt-4o-mini',
        'claude-opus-4-8', 'Qwen/Qwen3.6-32B'). Used for logging."""

    @abstractmethod
    def max_context_tokens(self) -> int:
        """Hard ceiling reported by the model. Distinct from
        ContextBudgetService.max_tokens (the platform's policy cap, which
        is typically lower than the model's hard ceiling)."""

    async def aclose(self) -> None:
        """Drain underlying HTTP/SDK clients.

        Default no-op for providers without persistent connections (mock,
        test-stub subclasses). Subclasses that hold a persistent client
        override this to drain it — see each provider's `aclose()` docstring
        for the specific call (`close()` on Stainless-wrapped SDKs vs.
        `aclose()` on raw `httpx.AsyncClient`). The agent process (Plan 10
        Task 11) calls this in its FastAPI lifespan shutdown handler.
        """
        return None
```

- [ ] **Step 4: Run lint + type check**

```bash
uv run ruff check src/codeoracle/adapters/llm/ src/codeoracle/core/exceptions.py
uv run ty check src/codeoracle/adapters/llm/interfaces.py
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/adapters/__init__.py src/codeoracle/adapters/llm/__init__.py \
        src/codeoracle/adapters/llm/interfaces.py src/codeoracle/core/exceptions.py \
        tests/unit/adapters/__init__.py tests/unit/adapters/llm/__init__.py
git commit -m "feat: Plan 4 Task 4 — LLMProvider ABC + LLMProviderError"
```

---

## Task 5: `src/codeoracle/adapters/llm/fingerprint.py` — request_fingerprint helper

**What this task delivers:** the deterministic hash function MockLLMProvider uses to look up scripted responses + the future fixture-recording tool will use to write them. Separated from `providers/mock.py` so future tooling doesn't import a provider module.

**Files:**
- Create: `src/codeoracle/adapters/llm/fingerprint.py`
- Create: `tests/unit/adapters/llm/test_fingerprint.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/fingerprint.py`**

```python
"""Request fingerprint — stable hash of the full spec §5.7 contract
(messages + tools + decoding params + structured-output target) used by
MockLLMProvider to look up scripted responses.

Stability properties:
  - The hash depends on `template_id`s and structural metadata, NOT raw
    `content` strings. Cosmetic edits to prompt templates (whitespace,
    rewording) do not invalidate fixtures.
  - When a message has no template_id, the hash falls back to a SHA256
    of the content. This is the ad-hoc-message path: cosmetic edits DO
    invalidate. Tests using ad-hoc messages must be aware of this.
  - The hash includes the spec §5.7 "key parameters": `model` (required,
    captures which provider config is in play), `temperature`, `max_tokens`,
    and `structured_output` (defaulted None — Plan 4 doesn't expose
    temperature/max_tokens per-call yet, but the signature is forward-
    compatible so a future per-call-decoding-param plan doesn't have to
    rewrite every fingerprint call-site).

Algorithm:
  Compose a canonical JSON object:
    {
      "messages": [
        {"role": ..., "template_id": ..., "content_hash": null_if_template_id_set}
        ...
      ],
      "tools": [{"name": ..., "parameters_schema": ...}, ...],
      "model": "<model-id>",
      "temperature": <float-or-null>,
      "max_tokens": <int-or-null>,
      "structured_output": "<module.QualName>-or-null"
    }
  Hash with SHA256, return hex digest.
"""

import hashlib
import json
from typing import Any

from codeoracle.models.llm import ChatMessage, ToolDefinition


def request_fingerprint(
    messages: list[ChatMessage],
    tools: list[ToolDefinition] | None = None,
    *,
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    structured_output: type | None = None,
) -> str:
    """Return SHA256 hex digest of the canonicalized request shape per
    spec §5.7's "key parameters" contract.

    Stable across cosmetic prompt edits IFF every ChatMessage has a
    template_id; otherwise stability is per-byte for the untemplated
    messages only. `model` is required (every real call has one — Mock
    passes `self.model_name()`); decoding params and the structured-output
    target default to None so call-sites stay terse until those params
    actually get exposed per-call.
    """
    canonical: dict[str, Any] = {
        "messages": [_canonicalize_message(m) for m in messages],
        "tools": [_canonicalize_tool(t) for t in (tools or [])],
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "structured_output": _canonicalize_structured_output(structured_output),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonicalize_message(m: ChatMessage) -> dict[str, Any]:
    """If template_id is set, hash by template_id; otherwise hash by content.

    The two paths are NOT interchangeable — a templated message and an
    ad-hoc message with identical text produce different fingerprints.
    This is intentional: it forces the test author to opt into stability.
    """
    if m.template_id is not None:
        return {
            "role": m.role,
            "template_id": m.template_id,
            "content_hash": None,  # explicit: stability comes from template_id
        }
    return {
        "role": m.role,
        "template_id": None,
        "content_hash": hashlib.sha256(m.content.encode("utf-8")).hexdigest(),
    }


def _canonicalize_tool(t: ToolDefinition) -> dict[str, Any]:
    return {
        "name": t.name,
        "parameters_schema": t.parameters_schema,
    }


def _canonicalize_structured_output(t: type | None) -> str | None:
    """Identify the structured-output target by fully-qualified name.

    Two different modules can both define `class Response`; module-path-
    qualified identity prevents fixture collisions across them. None means
    the call expects free-form text — distinct from any structured target.
    """
    if t is None:
        return None
    return f"{t.__module__}.{t.__qualname__}"
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/test_fingerprint.py`**

```python
"""Plan 4 Task 5: request_fingerprint unit tests.

Test convention: every call passes `model="test"` explicitly. `model` is a
required keyword arg per the spec §5.7 contract — making it required at
the type level forces every call-site (including future ones) to declare
which model the fixture corresponds to. Tests of the key-parameter axes
(model, temperature, max_tokens, structured_output) vary one axis at a
time and assert the hash changes.
"""

from codeoracle.adapters.llm.fingerprint import request_fingerprint
from codeoracle.models.llm import ChatMessage, ToolDefinition


def test_templated_messages_stable_under_content_edits():
    """Cosmetic edits to template content must not change the hash IF
    template_id is set. This is the load-bearing property of the
    fingerprint algorithm."""
    msg_v1 = ChatMessage(role="system", content="You are a helpful assistant.", template_id="sys_v1")
    msg_v2 = ChatMessage(role="system", content="You're a helpful assistant.",  template_id="sys_v1")
    assert request_fingerprint([msg_v1], model="test") == request_fingerprint([msg_v2], model="test")


def test_template_id_change_changes_hash():
    msg_a = ChatMessage(role="system", content="x", template_id="sys_v1")
    msg_b = ChatMessage(role="system", content="x", template_id="sys_v2")
    assert request_fingerprint([msg_a], model="test") != request_fingerprint([msg_b], model="test")


def test_untemplated_messages_hash_by_content():
    """If template_id is None, the hash falls back to content bytes."""
    msg_a = ChatMessage(role="user", content="hello")
    msg_b = ChatMessage(role="user", content="HELLO")
    assert request_fingerprint([msg_a], model="test") != request_fingerprint([msg_b], model="test")


def test_untemplated_identical_content_same_hash():
    msg_a = ChatMessage(role="user", content="hello")
    msg_b = ChatMessage(role="user", content="hello")
    assert request_fingerprint([msg_a], model="test") == request_fingerprint([msg_b], model="test")


def test_templated_and_untemplated_with_same_text_differ():
    """Forces test authors to opt into stability rather than accidentally
    relying on raw-content hashing for templated messages."""
    a = ChatMessage(role="user", content="hi")
    b = ChatMessage(role="user", content="hi", template_id="user_v1")
    assert request_fingerprint([a], model="test") != request_fingerprint([b], model="test")


def test_message_order_matters():
    a = ChatMessage(role="system", content="x", template_id="sys")
    b = ChatMessage(role="user", content="y", template_id="user")
    assert request_fingerprint([a, b], model="test") != request_fingerprint([b, a], model="test")


def test_tools_in_fingerprint():
    msgs = [ChatMessage(role="user", content="x", template_id="user")]
    tool = ToolDefinition(name="search", description="...", parameters_schema={"type": "object"})
    no_tools = request_fingerprint(msgs, model="test")
    with_tools = request_fingerprint(msgs, tools=[tool], model="test")
    assert no_tools != with_tools


def test_empty_tools_list_same_as_none():
    msgs = [ChatMessage(role="user", content="x", template_id="user")]
    assert (
        request_fingerprint(msgs, tools=[], model="test")
        == request_fingerprint(msgs, tools=None, model="test")
    )


def test_hash_is_hex_string_length_64():
    msgs = [ChatMessage(role="user", content="x", template_id="user")]
    h = request_fingerprint(msgs, model="test")
    assert len(h) == 64
    int(h, 16)  # validates hex


def test_model_change_changes_hash():
    """Spec §5.7 "key parameters" axis 1: model. The same messages routed to
    two different models must NOT collide on the same fixture — the fixture
    is implicitly model-specific."""
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    assert (
        request_fingerprint(msgs, model="gpt-4o-mini")
        != request_fingerprint(msgs, model="claude-opus-4-8")
    )


def test_temperature_change_changes_hash():
    """Spec §5.7 "key parameters" axis 2: temperature. A deterministic call
    (temperature=0) and a sampled call (temperature=0.7) get different
    fixtures even with identical messages."""
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    assert (
        request_fingerprint(msgs, model="test", temperature=0.0)
        != request_fingerprint(msgs, model="test", temperature=0.7)
    )


def test_max_tokens_change_changes_hash():
    """Spec §5.7 "key parameters" axis 3: max_tokens. A 100-token cap and a
    4000-token cap can plausibly elicit different responses — fixtures must
    be ceiling-specific so a future per-call max_tokens caller doesn't
    silently inherit a fixture recorded under a different ceiling."""
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    assert (
        request_fingerprint(msgs, model="test", max_tokens=100)
        != request_fingerprint(msgs, model="test", max_tokens=4000)
    )


def test_structured_output_change_changes_hash():
    """Spec §5.7 "key parameters" axis 4: structured_output. A free-form
    call (structured_output=None), a call expecting a `ResponseA` schema,
    and a call expecting `ResponseB` all resolve to DIFFERENT fixtures —
    even with identical messages — because the response shape they expect
    is part of the contract being mocked."""
    class ResponseA: pass
    class ResponseB: pass
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    free_form = request_fingerprint(msgs, model="test")
    schema_a = request_fingerprint(msgs, model="test", structured_output=ResponseA)
    schema_b = request_fingerprint(msgs, model="test", structured_output=ResponseB)
    assert free_form != schema_a
    assert free_form != schema_b
    assert schema_a != schema_b


def test_structured_output_qualname_distinguishes_same_named_classes():
    """Two `class Response` defined in different scopes must not collide —
    the canonical form is `<module>.<qualname>`, not just the class name."""
    def make_a():
        class Response: pass
        return Response
    def make_b():
        class Response: pass
        return Response
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    fp_a = request_fingerprint(msgs, model="test", structured_output=make_a())
    fp_b = request_fingerprint(msgs, model="test", structured_output=make_b())
    assert fp_a != fp_b
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/unit/adapters/llm/test_fingerprint.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 4: Run lint + type**

```bash
uv run ruff check src/codeoracle/adapters/llm/fingerprint.py tests/unit/adapters/llm/test_fingerprint.py
uv run ty check src/codeoracle/adapters/llm/fingerprint.py
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/adapters/llm/fingerprint.py tests/unit/adapters/llm/test_fingerprint.py
git commit -m "feat: Plan 4 Task 5 — request_fingerprint helper (template-stable hash)"
```

---

## Task 6: `src/codeoracle/adapters/llm/config.py` — LLMConfig + per-provider sub-configs

**What this task delivers:** the BaseSettings subclass that drives `LLMProviderFactory`. Reads `CODEORACLE_LLM_*` env vars (the double-underscore nested delimiter convention from Plan 3). Has a `@model_validator(mode="after")` that fails-fast at construction if the selected provider's sub-config is None.

**Files:**
- Create: `src/codeoracle/adapters/llm/config.py`
- Create: `tests/unit/adapters/llm/test_config.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/config.py`**

```python
"""LLM provider configuration. Reads env vars CODEORACLE_LLM_* (double-underscore
nested delimiter matches Plan 3's core/config.py convention).

Env shape examples (all use CODEORACLE_LLM_ prefix):
  CODEORACLE_LLM_PROVIDER=openai          → LLMConfig.provider == "openai"
  CODEORACLE_LLM_OPENAI__API_KEY=sk-...   → LLMConfig.openai.api_key.get_secret_value() == "sk-..."
  CODEORACLE_LLM_OPENAI__MODEL=gpt-4o-mini → LLMConfig.openai.model == "gpt-4o-mini"

Cross-validator (`_check_selected_provider_config_present`) raises at Settings
construction if CODEORACLE_LLM_PROVIDER=openai but CODEORACLE_LLM_OPENAI__API_KEY is unset.
This prevents the factory from blowing up at first-use (which would be
non-deterministic — depends on which service tries to chat first).
"""

import os
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_openai_model() -> str:
    """Default to pinned.env's OPENAI_DEV_MODEL if set in env, else gpt-4o-mini.

    pinned.env is sourced before tests / dev shells / docker-compose. If
    OPENAI_DEV_MODEL is not in env at Settings construction time, fall back
    to gpt-4o-mini (the value Plan 2 baselined). Production overrides via
    CODEORACLE_LLM_OPENAI__MODEL.
    """
    return os.environ.get("OPENAI_DEV_MODEL", "gpt-4o-mini")


class OpenAIProviderConfig(BaseModel):
    api_key: SecretStr = Field(min_length=1)
    """OpenAI API key. `SecretStr` so it never leaks via repr / `model_dump()` /
    exception / OTel span when this config is nested somewhere the name-keyed
    log redaction processor cannot reach; `.get_secret_value()` only at the SDK
    call site. Matches Plan 8's `SourcebotConfig.api_key`."""
    model: str = Field(default_factory=_default_openai_model)
    base_url: str | None = None
    """For OpenAI-compatible endpoints (e.g., a local proxy). None means
    the SDK's default endpoint."""
    max_context_tokens: int = Field(default=128_000, ge=1)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    """Total per-call read timeout (connect is a fixed 5s). Bounds the await so
    a stalled provider/proxy can't pin the calling coroutine past platform
    policy — the SDK's own multi-minute default would otherwise apply (D6-2)."""


class AnthropicProviderConfig(BaseModel):
    api_key: SecretStr = Field(min_length=1)
    model: str = Field(default="claude-opus-4-8", min_length=1)
    max_output_tokens: int = Field(default=4096, ge=1)  # Anthropic requires max_tokens on every request
    max_context_tokens: int = Field(default=200_000, ge=1)
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    """Total per-call read timeout (connect is a fixed 5s); 120s leaves room for
    long completions. See OpenAIProviderConfig (D6-2)."""


class VLLMProviderConfig(BaseModel):
    base_url: str = Field(default="http://vllm:8000/v1", min_length=1)
    """vLLM serves the OpenAI-compatible /v1/chat/completions shape."""
    model: str = Field(default="Qwen/Qwen3.6-32B", min_length=1)
    max_context_tokens: int = Field(default=32_768, ge=1)
    api_key: SecretStr = Field(default=SecretStr("EMPTY"), min_length=1)
    """vLLM's OpenAI-compatible server requires SOME api_key string; "EMPTY"
    is the convention for unauthenticated local servers. `SecretStr` for a
    uniform secret-typing convention across providers (Plan 8 parity)."""
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    """Total per-call read timeout (connect is a fixed 5s)."""


class OllamaProviderConfig(BaseModel):
    base_url: str = Field(default="http://ollama:11434", min_length=1)
    model: str = Field(default="qwen2.5:32b", min_length=1)
    max_context_tokens: int = Field(default=32_768, ge=1)
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    """Total per-call read timeout (connect is a fixed 5s); 120s covers a local
    model's cold-start latency."""


class MockLLMProviderConfig(BaseModel):
    scripts_dir: str = Field(default="tests/fixtures/llm_scripts", min_length=1)
    strict: bool = True
    """If True, unknown request fingerprints raise. If False, return a
    canned 'no fixture' response. Default True so test fixture rot is
    surfaced immediately."""


ProviderName = Literal["openai", "anthropic", "vllm", "ollama", "mock"]


class LLMConfig(BaseSettings):
    """Provider selection + per-provider config. Read from env at process boot.

    The factory at codeoracle.adapters.llm.factory uses this to construct a
    concrete LLMProvider. The cross-validator ensures the selected
    provider's sub-config is non-None — failing at Settings construction,
    not at the first chat() call.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_LLM_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: ProviderName = "openai"
    openai: OpenAIProviderConfig | None = None
    anthropic: AnthropicProviderConfig | None = None
    vllm: VLLMProviderConfig | None = None
    ollama: OllamaProviderConfig | None = None
    mock: MockLLMProviderConfig | None = None

    @model_validator(mode="after")
    def _check_selected_provider_config_present(self) -> "LLMConfig":
        selected: BaseModel | None = getattr(self, self.provider)
        if selected is None:
            raise ValueError(
                f"LLMConfig.provider='{self.provider}' but {self.provider} sub-config is None. "
                f"Set CODEORACLE_LLM_{self.provider.upper()}__* env vars (e.g., "
                f"CODEORACLE_LLM_{self.provider.upper()}__API_KEY for credential-bearing providers)."
            )
        return self
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/test_config.py`**

```python
"""Plan 4 Task 6: LLMConfig unit tests."""

import pytest
from pydantic import SecretStr, ValidationError

from codeoracle.adapters.llm.config import (
    AnthropicProviderConfig,
    LLMConfig,
    MockLLMProviderConfig,
    OpenAIProviderConfig,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Isolate config tests from developer shell + repo .env file.

    `monkeypatch.chdir(tmp_path)` escapes the repo's `.env` (pydantic-settings
    reads it from the CWD). Explicit `delenv` for each CODEORACLE_LLM_* var prevents
    cross-test pollution from the developer's shell — `monkeypatch.delenv` is
    auto-rolled-back at test teardown, so the developer's env is untouched
    outside this fixture's scope.
    """
    monkeypatch.chdir(tmp_path)
    for var in (
        "CODEORACLE_LLM_PROVIDER",
        "CODEORACLE_LLM_OPENAI__API_KEY",
        "CODEORACLE_LLM_OPENAI__MODEL",
        "CODEORACLE_LLM_OPENAI__BASE_URL",
        "CODEORACLE_LLM_ANTHROPIC__API_KEY",
        "CODEORACLE_LLM_ANTHROPIC__MODEL",
        "CODEORACLE_LLM_VLLM__BASE_URL",
        "CODEORACLE_LLM_VLLM__MODEL",
        "CODEORACLE_LLM_OLLAMA__BASE_URL",
        "CODEORACLE_LLM_OLLAMA__MODEL",
        "CODEORACLE_LLM_MOCK__SCRIPTS_DIR",
        "CODEORACLE_LLM_MOCK__STRICT",
        "OPENAI_DEV_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_openai_api_key_required():
    with pytest.raises(ValidationError):
        OpenAIProviderConfig()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        OpenAIProviderConfig(api_key="")  # empty rejected by min_length=1


def test_openai_api_key_is_secret_and_not_in_repr():
    """`api_key` is `SecretStr` so it never leaks via repr / str — the primary
    defense the log redaction processor cannot provide for nested configs
    (mirrors Plan 8's SourcebotConfig repr test)."""
    cfg = OpenAIProviderConfig(api_key="super-secret-do-not-log")
    assert isinstance(cfg.api_key, SecretStr)
    rendered = repr(cfg) + str(cfg.api_key)
    assert "super-secret-do-not-log" not in rendered
    assert cfg.api_key.get_secret_value() == "super-secret-do-not-log"


def test_openai_model_defaults_from_pinned_env(monkeypatch):
    monkeypatch.setenv("OPENAI_DEV_MODEL", "gpt-4o-test-pin")
    cfg = OpenAIProviderConfig(api_key="sk-x")
    assert cfg.model == "gpt-4o-test-pin"


def test_openai_model_default_when_pinned_env_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_DEV_MODEL", raising=False)
    cfg = OpenAIProviderConfig(api_key="sk-x")
    assert cfg.model == "gpt-4o-mini"


def test_anthropic_api_key_required():
    with pytest.raises(ValidationError):
        AnthropicProviderConfig()  # type: ignore[call-arg]


def test_mock_config_defaults():
    cfg = MockLLMProviderConfig()
    assert cfg.strict is True
    assert "llm_scripts" in cfg.scripts_dir


def test_llmconfig_default_provider_openai_requires_openai_subconfig(monkeypatch):
    monkeypatch.delenv("CODEORACLE_LLM_OPENAI__API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig()
    assert "openai sub-config is None" in str(exc_info.value)


def test_llmconfig_openai_via_env(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("CODEORACLE_LLM_OPENAI__API_KEY", "sk-test")
    monkeypatch.setenv("CODEORACLE_LLM_OPENAI__MODEL", "gpt-4o-test")
    cfg = LLMConfig()
    assert cfg.provider == "openai"
    assert cfg.openai is not None
    assert cfg.openai.api_key.get_secret_value() == "sk-test"
    assert cfg.openai.model == "gpt-4o-test"


def test_llmconfig_anthropic_selected_but_no_subconfig_raises(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("CODEORACLE_LLM_ANTHROPIC__API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig()
    assert "anthropic sub-config is None" in str(exc_info.value)


def test_llmconfig_vllm_no_credentials_required(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "vllm")
    # vllm sub-config has all defaults — supplying empty triggers the validator path
    monkeypatch.setenv("CODEORACLE_LLM_VLLM__BASE_URL", "http://localhost:8000/v1")
    cfg = LLMConfig()
    assert cfg.provider == "vllm"
    assert cfg.vllm is not None
    assert cfg.vllm.base_url == "http://localhost:8000/v1"


def test_llmconfig_mock_provider(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CODEORACLE_LLM_MOCK__STRICT", "false")
    cfg = LLMConfig()
    assert cfg.provider == "mock"
    assert cfg.mock is not None
    assert cfg.mock.strict is False


def test_llmconfig_invalid_provider_rejected(monkeypatch):
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "bogus")
    monkeypatch.setenv("CODEORACLE_LLM_OPENAI__API_KEY", "sk-x")
    with pytest.raises(ValidationError):
        LLMConfig()
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/unit/adapters/llm/test_config.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 4: Run lint + type**

```bash
uv run ruff check src/codeoracle/adapters/llm/config.py tests/unit/adapters/llm/test_config.py
uv run ty check src/codeoracle/adapters/llm/config.py
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/adapters/llm/config.py tests/unit/adapters/llm/test_config.py
git commit -m "feat: Plan 4 Task 6 — LLMConfig + cross-validator (fail-fast)"
```

---

## Task 7: `src/codeoracle/adapters/llm/providers/mock.py` — MockLLMProvider + YAML script loader

**What this task delivers:** the test-only provider that loads scripted responses from `tests/fixtures/llm_scripts/<name>.yaml` keyed by request fingerprint. Strict mode (default) raises on unknown fingerprints — forces fixture maintenance.

**Files:**
- Create: `src/codeoracle/adapters/llm/providers/__init__.py` (empty)
- Create: `src/codeoracle/adapters/llm/providers/mock.py`
- Create: `tests/fixtures/llm_scripts/test_explain_existing_symbol.yaml` (one canonical example)
- Create: `tests/unit/adapters/llm/providers/__init__.py` (empty)
- Create: `tests/unit/adapters/llm/providers/test_mock.py`

- [ ] **Step 1: Create the provider package + test directory**

```bash
touch src/codeoracle/adapters/llm/providers/__init__.py
mkdir -p tests/unit/adapters/llm/providers
touch tests/unit/adapters/llm/providers/__init__.py
mkdir -p tests/fixtures/llm_scripts
```

- [ ] **Step 2: Write the canonical fixture `tests/fixtures/llm_scripts/test_explain_existing_symbol.yaml`**

```yaml
# Canonical example. Plan 13 adds more fixtures keyed by the 9 slice tests.
# The fingerprint is the SHA256 emitted by request_fingerprint() — see
# tests/unit/adapters/llm/test_fingerprint.py for the algorithm.
# Generate fingerprints in dev shell (model arg is REQUIRED — match the
# MockLLMProvider's model_name(), which is "mock"):
#   uv run python -c "from codeoracle.adapters.llm.fingerprint import request_fingerprint; \
#     from codeoracle.models.llm import ChatMessage; \
#     print(request_fingerprint([ChatMessage(role='user', content='ping', template_id='ping_v1')], model='mock'))"

- request_fingerprint: "0000000000000000000000000000000000000000000000000000000000000000"
  response:
    content: '{"intent":"explain_symbol","symbol":"codeoracle.services.verifier.service.VerifierService.verify"}'
    usage:
      input_tokens: 84
      output_tokens: 32
    finish_reason: "stop"
    tool_calls: []
```

(The all-zeros fingerprint is a placeholder — the test in this task uses a generated fingerprint, not this one. The file exists so Plan 13's test fixtures have a directory to live in and a format example.)

- [ ] **Step 3: Write `src/codeoracle/adapters/llm/providers/mock.py`**

```python
"""MockLLMProvider — test-only provider that returns scripted responses keyed
by request fingerprint.

Behavior:
  - On __init__: loads ALL .yaml files under scripts_dir into an
    in-memory dict[fingerprint, ChatResponse].
  - On chat(): computes the fingerprint of (messages, tools), looks it up,
    returns the canned ChatResponse.
  - On unknown fingerprint with strict=True (default): raises
    LLMProviderError. With strict=False: returns a default "no fixture"
    response (content=""; usage zero; finish_reason="stop").

Token-counting stub: returns len(text) // 4. Deterministic, provider-free.
"""

from pathlib import Path

import yaml

from codeoracle.adapters.llm.config import MockLLMProviderConfig
from codeoracle.adapters.llm.fingerprint import request_fingerprint
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class MockLLMProvider(LLMProvider):
    """Deterministic provider for unit tests. NEVER used in production paths."""

    def __init__(self, config: MockLLMProviderConfig) -> None:
        self._config = config
        self._scripts: dict[str, ChatResponse] = self._load_scripts(config.scripts_dir)

    @staticmethod
    def _load_scripts(scripts_dir: str) -> dict[str, ChatResponse]:
        out: dict[str, ChatResponse] = {}
        root = Path(scripts_dir)
        if not root.is_dir():
            return out  # no fixtures yet — strict mode will raise on first chat()
        for yaml_file in sorted(root.glob("*.yaml")):
            entries = yaml.safe_load(yaml_file.read_text()) or []
            for entry in entries:
                fp = entry["request_fingerprint"]
                raw = entry["response"]
                response = ChatResponse(
                    content=raw["content"],
                    usage=TokenUsage(**raw["usage"]),
                    finish_reason=raw["finish_reason"],
                    tool_calls=[ToolCall(**tc) for tc in raw.get("tool_calls", [])],
                )
                if fp in out:
                    raise LLMProviderError(
                        f"duplicate request_fingerprint '{fp}' in {yaml_file}"
                    )
                out[fp] = response
        return out

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        # Fingerprint the FULL spec §5.7 contract — model + structured_output
        # included so a request to a different mock-model identifier or
        # expecting a different schema resolves to a different fixture.
        # temperature/max_tokens default to None at this layer (Plan 4 doesn't
        # expose them per-call); a future plan that adds per-call decoding
        # params updates this call-site without changing the signature.
        fp = request_fingerprint(
            messages,
            tools=tools,
            model=self.model_name(),
            structured_output=structured_output,
        )
        if fp in self._scripts:
            return self._scripts[fp]
        if self._config.strict:
            raise LLMProviderError(
                f"MockLLMProvider: no fixture for fingerprint {fp}. "
                f"Add an entry to a YAML file under {self._config.scripts_dir}/."
            )
        return ChatResponse(
            content="",
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            finish_reason="stop",
            tool_calls=[],
        )

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def model_name(self) -> str:
        return "mock"

    def max_context_tokens(self) -> int:
        return 1_000_000  # mock doesn't impose a real ceiling
```

- [ ] **Step 4: Write `tests/unit/adapters/llm/providers/test_mock.py`**

```python
"""Plan 4 Task 7: MockLLMProvider unit tests."""

import pytest
import yaml

from codeoracle.adapters.llm.config import MockLLMProviderConfig
from codeoracle.adapters.llm.fingerprint import request_fingerprint
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage


def _write_fixture(scripts_dir, name: str, fingerprint: str, content: str) -> None:
    """Write one fixture entry to a YAML file."""
    (scripts_dir / f"{name}.yaml").write_text(yaml.safe_dump([
        {
            "request_fingerprint": fingerprint,
            "response": {
                "content": content,
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "finish_reason": "stop",
                "tool_calls": [],
            },
        },
    ]))


async def test_mock_returns_scripted_response_on_known_fingerprint(tmp_path):
    msgs = [ChatMessage(role="user", content="ping", template_id="ping_v1")]
    # Match MockLLMProvider.chat()'s fingerprinting: model=model_name()="mock",
    # structured_output=None — keeps the seeded fingerprint identical to the
    # one the provider will compute at lookup time.
    fp = request_fingerprint(msgs, model="mock")
    _write_fixture(tmp_path, "test_one", fp, "pong")

    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=True),    )
    response = await provider.chat(msgs)
    assert response.content == "pong"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 20
    assert response.finish_reason == "stop"


async def test_mock_strict_unknown_fingerprint_raises(tmp_path):
    """Strict mode = fixture rot must surface immediately."""
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=True),    )
    msgs = [ChatMessage(role="user", content="hi", template_id="hi_v1")]
    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat(msgs)
    assert "no fixture" in str(exc_info.value)


async def test_mock_non_strict_unknown_fingerprint_returns_default(tmp_path):
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=False),    )
    msgs = [ChatMessage(role="user", content="hi", template_id="hi_v1")]
    response = await provider.chat(msgs)
    assert response.content == ""
    assert response.usage.input_tokens == 0
    assert response.finish_reason == "stop"


async def test_mock_duplicate_fingerprint_in_fixture_raises(tmp_path):
    """Loading two fixtures with the same fingerprint must fail at __init__."""
    msgs = [ChatMessage(role="user", content="x", template_id="t1")]
    fp = request_fingerprint(msgs, model="mock")
    _write_fixture(tmp_path, "a", fp, "first")
    _write_fixture(tmp_path, "b", fp, "second")
    with pytest.raises(LLMProviderError) as exc_info:
        MockLLMProvider(
            MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=True),
        )
    assert "duplicate request_fingerprint" in str(exc_info.value)


async def test_mock_count_tokens_deterministic():
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir="nonexistent_dir", strict=False),    )
    assert provider.count_tokens("12345678") == 2  # 8 // 4
    assert provider.count_tokens("") == 0


async def test_mock_model_name():
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir="nonexistent_dir", strict=False),    )
    assert provider.model_name() == "mock"


async def test_mock_missing_scripts_dir_loads_empty_no_error(tmp_path):
    """Bootstrap case — directory doesn't exist yet. Should not raise; strict
    mode still surfaces on first chat()."""
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path / "does_not_exist"), strict=False),    )
    msgs = [ChatMessage(role="user", content="x", template_id="t1")]
    response = await provider.chat(msgs)
    assert response.content == ""
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/unit/adapters/llm/providers/test_mock.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 6: Run lint + type**

```bash
uv run ruff check src/codeoracle/adapters/llm/providers/mock.py tests/unit/adapters/llm/providers/test_mock.py
uv run ty check src/codeoracle/adapters/llm/providers/mock.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/adapters/llm/providers/__init__.py \
        src/codeoracle/adapters/llm/providers/mock.py \
        tests/fixtures/llm_scripts/test_explain_existing_symbol.yaml \
        tests/unit/adapters/llm/providers/__init__.py \
        tests/unit/adapters/llm/providers/test_mock.py
git commit -m "feat: Plan 4 Task 7 — MockLLMProvider + YAML script loader"
```

---

## Task 8: `src/codeoracle/adapters/llm/providers/openai.py` — OpenAIProvider (DEV DEFAULT)

**What this task delivers:** the production-relevant provider, dev-default per memory `project-dev-llm-provider-openai`. Constructs `AsyncOpenAI` once in `__init__`, reuses across calls. Uses `tiktoken` for `count_tokens`. Wraps SDK exceptions in `LLMProviderError`.

**Files:**
- Create: `src/codeoracle/adapters/llm/providers/openai.py`
- Create: `tests/unit/adapters/llm/providers/test_openai.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/providers/openai.py`**

```python
"""OpenAIProvider — the dev-default LLM provider.

Constructs AsyncOpenAI once in __init__; reuses across chat() calls (per
SDK guidance: avoid one-client-per-call). Uses tiktoken for count_tokens
(sync, no API call).

This file is the ONLY location where `import openai` outside the SDK
itself is allowed — enforced by ruff TID251 per-file-ignore.
"""

import httpx
import openai  # noqa: TID251  — allowed only here
import tiktoken
from openai import AsyncOpenAI
from pydantic import BaseModel

from codeoracle.adapters.llm.config import OpenAIProviderConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class OpenAIProvider(LLMProvider):
    def __init__(self, config: OpenAIProviderConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,  # None → SDK default
            # Bound every call (D6-2): without these the SDK inherits its own
            # ~600s ceiling × internal retries (~30 min) — far past any platform
            # policy. connect=5s fails fast on an unreachable endpoint;
            # max_retries=0 because retry is the worker RetryPolicy's job, not a
            # hidden SDK multiplier.
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            max_retries=0,
        )
        try:
            self._encoding = tiktoken.encoding_for_model(config.model)
        except KeyError:
            # Unknown model id — fall back to o200k_base, the encoding the
            # 4o-class (gpt-4o*, o-series) dev/prod models use. Earlier
            # versions fell back to cl100k_base; that is the gpt-3.5/gpt-4
            # encoding and under-counts 4o-class tokens, so the budget gate
            # leaked. encoding_for_model() already returns the exact encoding
            # for every recognized model; this branch only catches unrecognized
            # ids and picks the most-likely-correct modern default.
            self._encoding = tiktoken.get_encoding("o200k_base")

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._config.model,
                messages=[_to_openai_message(m) for m in messages],
                tools=[_to_openai_tool(t) for t in (tools or [])] or None,
                # structured_output → OpenAI Structured Outputs. When the caller
                # passes a Pydantic model, _to_response_format() emits the
                # strict json_schema response_format so the decoder is
                # constrained to schema-valid JSON (returned as text content the
                # caller parses). None → omit the param entirely (free-form).
                response_format=(
                    _to_response_format(structured_output)
                    if structured_output is not None
                    else openai.NOT_GIVEN
                ),
            )
        except openai.OpenAIError as exc:
            raise LLMProviderError(f"OpenAI SDK error: {exc}") from exc

        choice = response.choices[0]
        return ChatResponse(
            content=choice.message.content or "",
            usage=TokenUsage(
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
            ),
            finish_reason=_map_finish_reason(choice.finish_reason),
            tool_calls=[
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments_json=tc.function.arguments,
                )
                for tc in (choice.message.tool_calls or [])
            ],
        )

    def count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain the AsyncOpenAI HTTP client (Plan 10 Task 11 calls in lifespan shutdown)."""
        await self._client.close()


def _to_openai_message(m: ChatMessage) -> dict[str, object]:
    """Serialize a ChatMessage into OpenAI's wire shape.

    Plan 4 scope: role + content + tool_call_id (for tool-result turns
    feeding back into the model).

    INTENTIONALLY OMITS assistant `m.tool_calls` — the field is kept in the
    ChatMessage model (spec §5.2) for the eventual multi-turn agent loop, but
    Plan 4 is a single-shot slice and never replays a prior assistant turn, so
    serializing it here would be dead code. Multi-turn flows where the agent
    loop sends back a prior assistant message containing tool calls need
    ``{"role":"assistant","content":"...","tool_calls":[...]}`` on the OpenAI
    wire. OWNER: Plan 10 (the agent process) introduces the multi-turn loop and
    will extend this helper to emit the tool_calls array when ``m.tool_calls``
    is non-empty. The omission is pinned by
    ``test_to_openai_message_omits_tool_calls`` so a future edit that starts
    emitting them is a conscious, test-breaking change, not an accident.
    """
    out: dict[str, object] = {"role": m.role, "content": m.content}
    if m.tool_call_id is not None:
        out["tool_call_id"] = m.tool_call_id
    return out


def _to_openai_tool(t: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters_schema,
        },
    }


def _to_response_format(target: type) -> dict[str, object]:
    """Build OpenAI's strict `json_schema` response_format from a Pydantic model.

    OpenAI Structured Outputs (strict mode) constrain decoding so the model
    cannot emit schema-invalid JSON. Strict mode imposes two rules on every
    object node: ``additionalProperties: false``, AND **every** property listed
    in ``required`` (strict mode does not honor optionality-by-omission —
    optionality must be expressed as a nullable type, not a missing key).
    Pydantic's ``model_json_schema()`` emits neither by default — it omits
    ``additionalProperties`` entirely and lists only non-defaulted fields in
    ``required`` — so ``_enforce_strict_object_schema`` sets both on every
    object node. A model with a defaulted field would otherwise yield a schema
    OpenAI rejects with a 400; forcing all properties into ``required`` makes the
    schema strict-mode-valid (the model still applies its own defaults when it
    parses the returned JSON).

    Only a `pydantic.BaseModel` subclass carries a JSON schema we can emit.
    Any other `type` (the ABC parameter is typed `type | None`) cannot be
    honored, so we raise rather than silently drop the constraint — a dropped
    schema would return unvalidated output the caller believes is validated.
    """
    if not (isinstance(target, type) and issubclass(target, BaseModel)):
        raise LLMProviderError(
            f"OpenAI structured_output requires a pydantic.BaseModel subclass; "
            f"got {target!r}. Wrap the target shape in a Pydantic model."
        )
    schema = target.model_json_schema()
    _enforce_strict_object_schema(schema)
    return {
        "type": "json_schema",
        "json_schema": {"name": target.__name__, "schema": schema, "strict": True},
    }


def _enforce_strict_object_schema(node: object) -> None:
    """Make every object node OpenAI-strict-mode-valid, in place.

    For each ``{"type": "object", ...}`` node: set ``additionalProperties:
    false`` and set ``required`` to ALL declared property names. OpenAI strict
    Structured Outputs reject object nodes that allow extra properties OR that
    leave any property out of ``required``. Pydantic emits neither guarantee, so
    we walk the whole schema tree (``properties`` values, ``$defs``, ``items``,
    and any nested object) and enforce both. Pure mutation of a plain dict/list
    tree — a free helper, not a class.
    """
    if isinstance(node, dict):
        if node.get("type") == "object":
            node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
        for value in node.values():
            _enforce_strict_object_schema(value)
    elif isinstance(node, list):
        for item in node:
            _enforce_strict_object_schema(item)


def _map_finish_reason(reason: str | None) -> str:
    """OpenAI uses 'stop' | 'length' | 'tool_calls' | 'content_filter' | 'function_call'.
    Map to our enum (which matches the first four). 'function_call' is legacy
    OpenAI shape; treat as 'tool_calls'."""
    if reason == "function_call":
        return "tool_calls"
    if reason in ("stop", "length", "tool_calls", "content_filter"):
        return reason
    return "stop"  # unknown → benign default
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/providers/test_openai.py`**

These are unit tests — they use `respx` to mock the OpenAI HTTPS endpoint, so no real OpenAI calls happen.

```python
"""Plan 4 Task 8: OpenAIProvider unit tests (respx-mocked).

The live-LLM smoke test lives in tests/integration/llm/ (Task 14).
"""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from codeoracle.adapters.llm.config import OpenAIProviderConfig
from codeoracle.adapters.llm.providers.openai import OpenAIProvider, _to_openai_message
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage, ToolCall


class _Intent(BaseModel):
    intent: str
    confidence: float
    note: str = ""  # defaulted — Pydantic omits it from `required`, but OpenAI
    #                 strict mode demands ALL properties in `required`.


def _provider() -> OpenAIProvider:
    return OpenAIProvider(
        OpenAIProviderConfig(api_key="sk-test", model="gpt-4o-mini"),    )


def test_openai_client_is_bounded_and_disables_sdk_retries():
    """D6-2: the SDK client must carry an explicit per-call timeout and
    max_retries=0. The SDK's own ~600s × internal-retry default is not a
    platform-reasoned ceiling, and retry is the worker RetryPolicy's job.
    (Asserts the constructed client's effective settings.)"""
    p = OpenAIProvider(
        OpenAIProviderConfig(
            api_key="sk-test", model="gpt-4o-mini", request_timeout_seconds=42.0
        ),    )
    assert p._client.max_retries == 0
    assert p._client.timeout.read == 42.0
    assert p._client.timeout.connect == 5.0


async def test_openai_chat_happy_path():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-x",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gpt-4o-mini",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
            )
        )
        provider = _provider()
        response = await provider.chat([ChatMessage(role="user", content="ping")])
        assert response.content == "pong"
        assert response.usage.input_tokens == 5
        assert response.usage.output_tokens == 1
        assert response.finish_reason == "stop"


async def test_openai_chat_wraps_sdk_error_in_llm_provider_error():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                500,
                json={"error": {"message": "internal server error", "type": "server_error"}},
            )
        )
        provider = _provider()
        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat([ChatMessage(role="user", content="ping")])
        assert "OpenAI SDK error" in str(exc_info.value)


async def test_openai_count_tokens_uses_tiktoken():
    """count_tokens must be tiktoken-backed, not a naive split.

    Avoid asserting exact counts — tiktoken version pins can shift counts for
    short strings, and the goal here is "is this actually tiktoken-driven?",
    not "does tiktoken 0.7.0 produce exactly 1 token for 'hello'?".
    Use a length-monotonicity invariant instead: longer text → more tokens.
    """
    provider = _provider()
    short = provider.count_tokens("hello")
    longer = provider.count_tokens("the quick brown fox jumps over the lazy dog")
    assert short >= 1, "tiktoken should produce >=1 token for non-empty input"
    assert longer > short, "longer text should produce more tokens"


async def test_openai_count_tokens_unknown_model_falls_back_to_o200k():
    """Unknown model id must still tokenize — the fallback is o200k_base (the
    4o-class encoding), not cl100k_base (which under-counts modern models)."""
    provider = OpenAIProvider(
        OpenAIProviderConfig(api_key="sk-test", model="gpt-fictional-future"),    )
    n = provider.count_tokens("hello")
    assert n > 0


async def test_openai_model_name():
    provider = OpenAIProvider(
        OpenAIProviderConfig(api_key="sk-test", model="gpt-4o-mini"),    )
    assert provider.model_name() == "gpt-4o-mini"


async def test_openai_max_context_tokens_from_config():
    provider = OpenAIProvider(
        OpenAIProviderConfig(api_key="sk-test", model="gpt-4o-mini", max_context_tokens=16_000),    )
    assert provider.max_context_tokens() == 16_000


async def test_openai_chat_with_tool_calls_response():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-x",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gpt-4o-mini",
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "search", "arguments": '{"q":"x"}'},
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
                },
            )
        )
        provider = _provider()
        response = await provider.chat([ChatMessage(role="user", content="search for x")])
        assert response.finish_reason == "tool_calls"
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "search"
        assert response.tool_calls[0].arguments_json == '{"q":"x"}'


async def test_openai_structured_output_sends_strict_json_schema():
    """D-4.5: a Pydantic structured_output target is sent as OpenAI strict
    Structured Outputs. Asserts the OUTBOUND request carries
    response_format.json_schema with strict=True and additionalProperties=false
    on the object node — i.e. the constraint actually reaches the wire."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-x",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gpt-4o-mini",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"intent":"explain","confidence":0.9}'},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
                },
            )
        )
        provider = _provider()
        response = await provider.chat(
            [ChatMessage(role="user", content="classify")], structured_output=_Intent
        )
        assert response.content == '{"intent":"explain","confidence":0.9}'
        sent = json.loads(route.calls.last.request.content)
        rf = sent["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "_Intent"
        assert rf["json_schema"]["strict"] is True
        schema = rf["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        # Strict mode demands EVERY property in `required` — including the
        # defaulted `note`, which Pydantic would otherwise omit. Without this
        # the live call 400s on any model carrying an optional field.
        assert set(schema["required"]) == {"intent", "confidence", "note"}


async def test_openai_structured_output_rejects_non_pydantic_type():
    """D-4.5: a non-Pydantic structured_output target cannot produce a JSON
    schema, so the provider raises (loud) rather than silently dropping the
    constraint. Raises at request-build time — no HTTP call is attempted."""
    provider = _provider()
    with pytest.raises(LLMProviderError, match="pydantic.BaseModel"):
        await provider.chat([ChatMessage(role="user", content="x")], structured_output=dict)


def test_to_openai_message_omits_tool_calls():
    """D-4.9: the outbound converter INTENTIONALLY drops assistant tool_calls
    (single-shot slice; multi-turn replay is owned by Plan 10). This pins the
    omission so a future edit that begins emitting them is a conscious,
    test-breaking change. content + role survive; tool_call_id survives for
    tool-result turns; tool_calls does not appear on the wire dict."""
    msg = ChatMessage(
        role="assistant",
        content="calling",
        tool_calls=[ToolCall(id="call_1", name="search", arguments_json='{"q":"x"}')],
    )
    wire = _to_openai_message(msg)
    assert wire == {"role": "assistant", "content": "calling"}
    assert "tool_calls" not in wire
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/unit/adapters/llm/providers/test_openai.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 4: Run lint + type**

```bash
uv run ruff check src/codeoracle/adapters/llm/providers/openai.py tests/unit/adapters/llm/providers/test_openai.py
uv run ty check src/codeoracle/adapters/llm/providers/openai.py
```

Expected: no errors. (The `import openai  # noqa: TID251` is the only line where ruff would normally object; per-file-ignore + the noqa cover it.)

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/adapters/llm/providers/openai.py tests/unit/adapters/llm/providers/test_openai.py
git commit -m "feat: Plan 4 Task 8 — OpenAIProvider (dev default)"
```

---

## Task 9: `src/codeoracle/adapters/llm/providers/anthropic.py` — AnthropicProvider

**What this task delivers:** the Anthropic implementation. `AsyncAnthropic` client constructed in `__init__` for `chat()`. For `count_tokens`, calls Anthropic's REAL token-counting API via a **synchronous** `anthropic.Anthropic` client (`client.messages.count_tokens(...)`) — no local Anthropic tokenizer exists, so this is the only exact counter. Results are memoized by content hash (template-stable text yields few distinct strings, so the network cost amortizes to near-zero). A labeled, logged `len(text) // 4` heuristic is the LAST-RESORT fallback used only when the count API itself errors (offline, auth, rate-limit) so a transient counting failure degrades the budget gate instead of crashing the call. The earlier `tiktoken.get_encoding("cl100k_base")` approximation is removed — `cl100k_base` is OpenAI's encoding and systematically mis-counts Claude tokens, defeating the budget gate it feeds.

**Files:**
- Create: `src/codeoracle/adapters/llm/providers/anthropic.py`
- Create: `tests/unit/adapters/llm/providers/test_anthropic.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/providers/anthropic.py`**

```python
"""AnthropicProvider — Anthropic Messages API.

Constructs AsyncAnthropic once in __init__ for chat(). count_tokens uses
Anthropic's REAL token-counting endpoint — there is no local Anthropic
tokenizer, so an exact count requires the API. Because the ABC's
``count_tokens`` is synchronous, we hold a SECOND, synchronous
``anthropic.Anthropic`` client purely for ``messages.count_tokens(...)``
(the sync client makes a blocking HTTPS call; no async bridge is needed).
Counts are memoized by content hash — prompt templates are stable, so the
set of distinct strings is small and the network cost amortizes away. If the
count API errors (offline / auth / rate-limit), we log a WARNING and fall back
to a labeled ``len(text) // 4`` heuristic so a transient counting failure
degrades the budget gate rather than crashing the chat() call.
"""

import hashlib
import json

import anthropic  # noqa: TID251 — allowed only here
import httpx
from anthropic import Anthropic, AsyncAnthropic

from codeoracle.core.logging import get_logger

from codeoracle.adapters.llm.config import AnthropicProviderConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class AnthropicProvider(LLMProvider):
    def __init__(self, config: AnthropicProviderConfig) -> None:
        self._config = config
        # Bound every call (D6-2; see OpenAIProvider): the SDK's own long
        # default × internal retries is not a platform-reasoned ceiling, and
        # retry is the worker RetryPolicy's job — disable the SDK multiplier.
        self._client = AsyncAnthropic(
            api_key=config.api_key.get_secret_value(),
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            max_retries=0,
        )
        # Separate SYNCHRONOUS client used ONLY by count_tokens(): the ABC's
        # count_tokens is sync, Anthropic has no local tokenizer, and the count
        # API is the only exact counter. The sync client blocks on its own
        # HTTPS call — no async bridge needed. Same bounded-timeout / no-retry
        # policy as the async client (D6-2).
        self._sync_client = Anthropic(
            api_key=config.api_key.get_secret_value(),
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            max_retries=0,
        )
        self._token_cache: dict[str, int] = {}
        self._logger = get_logger(__name__)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        if structured_output is not None:
            raise NotImplementedError(
                "AnthropicProvider does not implement structured_output. It is part of the "
                "spec §5.7 request-fingerprint contract, and the OpenAIProvider (dev/live "
                "default) implements it; Anthropic's native path is forced tool-use "
                "(a single tool whose input_schema is the target, with tool_choice fixed to "
                "that tool). DEFERRAL: no slice consumer routes structured_output through "
                "Anthropic — the slice runs on OpenAI. Plan 13 gates WHEN a non-OpenAI "
                "provider first goes live (its Deferred block: 'Live-LLM gate provider matrix "
                "… Plan 13 decides scope'); implementing this forced-tool-use path is a "
                "PRECONDITION of that go-live, built by whoever performs it — Plan 13 decides "
                "the timing, not the implementation. Failing loud rather than silently dropping "
                "the constraint (a silently-ignored schema would return unvalidated output)."
            )

        system_prompt, user_assistant_messages = _split_system(messages)

        try:
            response = await self._client.messages.create(
                model=self._config.model,
                system=system_prompt or anthropic.NOT_GIVEN,
                messages=[_to_anthropic_message(m) for m in user_assistant_messages],
                tools=[_to_anthropic_tool(t) for t in (tools or [])] or anthropic.NOT_GIVEN,
                max_tokens=self._config.max_output_tokens,  # Anthropic requires this; configurable, default 4096
            )
        except anthropic.AnthropicError as exc:
            raise LLMProviderError(f"Anthropic SDK error: {exc}") from exc

        content_text = ""
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments_json=json.dumps(block.input),
                ))

        return ChatResponse(
            content=content_text,
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            finish_reason=_map_finish_reason(response.stop_reason),
            tool_calls=tool_calls,
        )

    def count_tokens(self, text: str) -> int:
        """Exact Anthropic token count via the count-tokens API, memoized by
        content hash. Falls back to a labeled len//4 heuristic ONLY if the API
        errors (logged WARNING) so a transient counting failure degrades the
        budget gate instead of crashing the call."""
        if not text:
            return 0
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._token_cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._sync_client.messages.count_tokens(
                model=self._config.model,
                messages=[{"role": "user", "content": text}],
            )
            count = result.input_tokens
        except anthropic.AnthropicError as exc:
            # Last-resort fallback — NOT silent: surfaced so the operator sees
            # the budget gate is running on an approximation, not the real count.
            count = max(1, len(text) // 4)
            self._logger.warning(
                "anthropic_count_tokens_api_failed_using_heuristic",
                error=str(exc),
                model=self._config.model,
                heuristic="len//4",
                approx_tokens=count,
            )
        self._token_cache[key] = count
        return count

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain both Anthropic clients (Plan 10 Task 11 calls in lifespan
        shutdown). The async client is awaited; the sync count-tokens client is
        closed synchronously (it exposes a blocking ``close()``, not a coro)."""
        await self._client.close()
        self._sync_client.close()


def _split_system(messages: list[ChatMessage]) -> tuple[str, list[ChatMessage]]:
    """Anthropic API takes 'system' as a top-level param, not a message role."""
    system_parts: list[str] = []
    rest: list[ChatMessage] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        else:
            rest.append(m)
    return ("\n\n".join(system_parts), rest)


def _to_anthropic_message(m: ChatMessage) -> dict[str, object]:
    """Serialize a ChatMessage into Anthropic's wire shape.

    Plan 4 scope: flat role + string-content. Sufficient for the
    single-turn smoke test (`pong` round-trip).

    INTENTIONALLY OMITS assistant `m.tool_calls` — the field is kept in the
    ChatMessage model (spec §5.2) for the eventual multi-turn agent loop, but
    Plan 4 is a single-shot slice and never replays a prior assistant turn.
    Multi-turn flows that feed back a prior assistant turn with tool uses need
    Anthropic's content-block array shape:
        [{"type": "text", "text": "..."},
         {"type": "tool_use", "id": "...", "name": "...", "input": {...}}]
    OWNER: Plan 10 (the agent process) introduces the multi-turn loop and will
    replace the flat-string branch with the content-block branch when
    ``m.tool_calls`` is non-empty. The omission is pinned by
    ``test_to_anthropic_message_omits_tool_calls`` so a future edit that starts
    emitting them is a conscious, test-breaking change, not an accident.
    """
    return {"role": m.role, "content": m.content}


def _to_anthropic_tool(t: ToolDefinition) -> dict[str, object]:
    return {
        "name": t.name,
        "description": t.description,
        "input_schema": t.parameters_schema,
    }


def _map_finish_reason(reason: str | None) -> str:
    """Anthropic: 'end_turn' | 'max_tokens' | 'stop_sequence' | 'tool_use'."""
    mapping = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
    }
    return mapping.get(reason or "", "stop")
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/providers/test_anthropic.py`**

```python
"""Plan 4 Task 9: AnthropicProvider unit tests (respx-mocked)."""

import httpx
import pytest
import respx

from codeoracle.adapters.llm.config import AnthropicProviderConfig
from codeoracle.adapters.llm.providers.anthropic import (
    AnthropicProvider,
    _to_anthropic_message,
)
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage, ToolCall


def _provider() -> AnthropicProvider:
    return AnthropicProvider(
        AnthropicProviderConfig(api_key="sk-ant-test", model="claude-opus-4-8"),    )


def test_anthropic_client_is_bounded_and_disables_sdk_retries():
    """D6-2 (see OpenAIProvider): bound every call, disable the hidden SDK
    retry multiplier — retry is the worker RetryPolicy's job.
    (Asserts the constructed client's effective settings.)"""
    p = AnthropicProvider(
        AnthropicProviderConfig(
            api_key="sk-ant-test",
            model="claude-opus-4-8",
            request_timeout_seconds=99.0,
        ),    )
    assert p._client.max_retries == 0
    assert p._client.timeout.read == 99.0
    assert p._client.timeout.connect == 5.0


async def test_anthropic_chat_happy_path():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg_01",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "pong"}],
                    "model": "claude-opus-4-8",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                },
            )
        )
        provider = _provider()
        response = await provider.chat([ChatMessage(role="user", content="ping")])
        assert response.content == "pong"
        assert response.usage.input_tokens == 5
        assert response.finish_reason == "stop"


async def test_anthropic_wraps_sdk_error():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}}),
        )
        provider = _provider()
        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat([ChatMessage(role="user", content="ping")])
        assert "Anthropic SDK error" in str(exc_info.value)


async def test_anthropic_splits_system_from_messages():
    """System messages must go to the top-level `system` param, not into messages[]."""
    captured = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "model": "claude-opus-4-8",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(side_effect=capture)
        provider = _provider()
        await provider.chat([
            ChatMessage(role="system", content="be helpful"),
            ChatMessage(role="user", content="hi"),
        ])

    body = captured["body"]
    assert body["system"] == "be helpful"
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_anthropic_max_tokens_per_config():
    provider = AnthropicProvider(
        AnthropicProviderConfig(api_key="sk-ant-test", model="claude-opus-4-8", max_context_tokens=8192),    )
    assert provider.max_context_tokens() == 8192


async def test_anthropic_count_tokens_uses_api_and_caches():
    """count_tokens calls the real count-tokens API (via the sync client) and
    memoizes by content hash — a second identical call hits the cache, not the
    network. Empty string short-circuits to 0 with no API call."""
    route = respx.post("https://api.anthropic.com/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 7})
    )
    with respx.mock(assert_all_called=True):
        provider = _provider()
        assert provider.count_tokens("") == 0          # no API call
        assert provider.count_tokens("hello") == 7     # 1st call → API
        assert provider.count_tokens("hello") == 7     # 2nd call → cache
    assert route.call_count == 1, "second identical count must be served from cache"


async def test_anthropic_count_tokens_falls_back_to_heuristic_on_api_error():
    """If the count-tokens API errors, count_tokens degrades to a labeled
    len//4 heuristic (logged WARNING) instead of crashing the budget check."""
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.anthropic.com/v1/messages/count_tokens").mock(
            return_value=httpx.Response(503, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}),
        )
        provider = _provider()
        # "hello world!" is 12 chars → 12 // 4 == 3 via the heuristic fallback.
        assert provider.count_tokens("hello world!") == 3


def test_to_anthropic_message_omits_tool_calls():
    """D-4.9: the outbound converter INTENTIONALLY drops assistant tool_calls
    (single-shot slice; multi-turn replay with content-block tool_use is owned
    by Plan 10). This pins the omission so a future edit that begins emitting
    the content-block array is a conscious, test-breaking change. The wire dict
    stays flat role + string-content with no tool_use blocks."""
    msg = ChatMessage(
        role="assistant",
        content="calling",
        tool_calls=[ToolCall(id="toolu_1", name="search", arguments_json='{"q":"x"}')],
    )
    wire = _to_anthropic_message(msg)
    assert wire == {"role": "assistant", "content": "calling"}
    assert isinstance(wire["content"], str)  # flat string, not a content-block list
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/adapters/llm/providers/test_anthropic.py -v
uv run ruff check src/codeoracle/adapters/llm/providers/anthropic.py
uv run ty check src/codeoracle/adapters/llm/providers/anthropic.py
```

Expected: tests PASS; no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/adapters/llm/providers/anthropic.py tests/unit/adapters/llm/providers/test_anthropic.py
git commit -m "feat: Plan 4 Task 9 — AnthropicProvider"
```

---

## Task 9b: `src/codeoracle/adapters/llm/tokenizer_loader.py` — shared HF tokenizer load + heuristic fallback

**What this task delivers:** the one place that loads a served model's Hugging Face tokenizer (`tokenizers.Tokenizer.from_pretrained`) and the one place that defines the labeled `len // 4` last-resort fallback. Both VLLMProvider and OllamaProvider call these — factoring them here keeps the load-and-log-once logic and the fallback formula identical across providers (no drift, no duplicated heuristic — the §2 "duplicated heuristic" finding is closed structurally). Lives directly under `adapters/llm/` (NOT `providers/`) because it wraps an external library (`tokenizers`) but is provider-agnostic; it imports no banned SDK, so it needs no TID251 per-file-ignore. Two free functions (not a class) per the OOP-preference memory's carve-out for pure transformations.

**Files:**
- Create: `src/codeoracle/adapters/llm/tokenizer_loader.py`
- Create: `tests/unit/adapters/llm/test_tokenizer_loader.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/tokenizer_loader.py`**

```python
"""Shared Hugging Face tokenizer loading + heuristic fallback for the
local-model providers (vLLM, Ollama).

`load_hf_tokenizer(model_id)` fetches the model's `tokenizer.json` from the
Hub ONCE (then disk-cached by huggingface_hub) and returns a ready
`tokenizers.Tokenizer`, or None if it can't load (offline, gated/private repo,
or `model_id` isn't a Hub repo id — e.g. an Ollama tag like "qwen2.5:32b").
The WARNING is logged HERE, once per failed load, so each provider's
count_tokens stays a one-liner and the operator sees exactly one signal that
the budget gate is running on an approximation.

`count_tokens_with_fallback(tokenizer, text)` is the single definition of the
labeled `len // 4` fallback — used when `tokenizer is None`. Centralizing it
means the fallback formula can never drift between vLLM and Ollama.
"""

from tokenizers import Tokenizer

from codeoracle.core.logging import get_logger

_logger = get_logger(__name__)


def load_hf_tokenizer(model_id: str) -> Tokenizer | None:
    """Return the model's HF tokenizer, or None (logged WARNING) if it can't
    load. Called once per provider construction; the encode hot-path that uses
    the returned tokenizer makes zero network round-trips."""
    try:
        return Tokenizer.from_pretrained(model_id)
    except Exception as exc:  # noqa: BLE001 — any load failure degrades to heuristic
        # Broad catch is deliberate: from_pretrained raises a range of
        # exceptions (network, 401/404 from the Hub, malformed repo, value
        # errors on non-repo-id strings). ALL of them mean "no exact tokenizer
        # available" → fall back. Logged so the degradation is never silent.
        _logger.warning(
            "hf_tokenizer_load_failed_using_heuristic",
            model=model_id,
            error=str(exc),
            fallback="len//4",
        )
        return None


def count_tokens_with_fallback(tokenizer: Tokenizer | None, text: str) -> int:
    """Exact length via `tokenizer.encode(text)` when a tokenizer is present;
    otherwise the labeled `len(text) // 4` last-resort heuristic. Empty text is
    always 0. The single source of truth for the fallback formula."""
    if not text:
        return 0
    if tokenizer is not None:
        return len(tokenizer.encode(text).ids)
    return max(1, len(text) // 4)
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/test_tokenizer_loader.py`**

```python
"""Plan 4 Task 9b: tokenizer_loader unit tests. No network — the real-load path
is patched; only the fallback formula and the None-on-failure contract are
asserted here (a live load is exercised by the providers' integration path)."""

from codeoracle.adapters.llm import tokenizer_loader
from codeoracle.adapters.llm.tokenizer_loader import (
    count_tokens_with_fallback,
    load_hf_tokenizer,
)


class _StubEncoding:
    def __init__(self, n: int) -> None:
        self.ids = list(range(n))


class _StubTokenizer:
    def __init__(self, n: int) -> None:
        self._n = n

    def encode(self, text: str) -> _StubEncoding:
        return _StubEncoding(self._n)


def test_fallback_empty_is_zero():
    assert count_tokens_with_fallback(None, "") == 0
    assert count_tokens_with_fallback(_StubTokenizer(5), "") == 0


def test_fallback_heuristic_when_tokenizer_none():
    assert count_tokens_with_fallback(None, "abcdefgh") == 2  # 8 // 4
    assert count_tokens_with_fallback(None, "a") == 1         # max(1, 0)


def test_exact_count_when_tokenizer_present():
    assert count_tokens_with_fallback(_StubTokenizer(7), "anything") == 7


def test_load_returns_none_and_logs_on_failure(monkeypatch):
    """A non-repo-id string (Ollama tag) must yield None, not raise."""
    def _boom(model_id: str):
        raise ValueError(f"not a repo id: {model_id}")

    monkeypatch.setattr(tokenizer_loader.Tokenizer, "from_pretrained", staticmethod(_boom))
    assert load_hf_tokenizer("qwen2.5:32b") is None


def test_load_returns_tokenizer_on_success(monkeypatch):
    sentinel = _StubTokenizer(3)
    monkeypatch.setattr(
        tokenizer_loader.Tokenizer, "from_pretrained", staticmethod(lambda model_id: sentinel)
    )
    assert load_hf_tokenizer("Qwen/Qwen3.6-32B") is sentinel
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/adapters/llm/test_tokenizer_loader.py -v
uv run ruff check src/codeoracle/adapters/llm/tokenizer_loader.py tests/unit/adapters/llm/test_tokenizer_loader.py
uv run ty check src/codeoracle/adapters/llm/tokenizer_loader.py
```

Expected: 5 tests PASS; no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/adapters/llm/tokenizer_loader.py tests/unit/adapters/llm/test_tokenizer_loader.py
git commit -m "feat: Plan 4 Task 9b — shared HF tokenizer loader + heuristic fallback"
```

---

## Task 10: `src/codeoracle/adapters/llm/providers/vllm.py` — VLLMProvider (httpx + OpenAI-compatible API)

**What this task delivers:** the vLLM implementation. vLLM serves the OpenAI-compatible `/v1/chat/completions` shape; we use `httpx.AsyncClient` directly (NOT the OpenAI SDK against a `base_url`, because that would require credentials and vLLM auth is optional). `count_tokens` loads the served model's Hugging Face tokenizer locally via `tokenizers.Tokenizer.from_pretrained(config.model)` in `__init__` and encodes with zero per-call network round-trip — the EXACT tokenizer the model uses, so the budget gate is accurate. If the tokenizer can't load (offline, gated repo, or `config.model` isn't a Hub repo id), the provider logs a WARNING once and `count_tokens` falls back to a labeled `len(text) // 4` heuristic. This replaces the prior `len(text.split()) * 1.3` word-split, which mis-counted subword tokenization by a wide, model-dependent margin.

**Files:**
- Create: `src/codeoracle/adapters/llm/providers/vllm.py`
- Create: `tests/unit/adapters/llm/providers/test_vllm.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/providers/vllm.py`**

```python
"""VLLMProvider — vLLM serves OpenAI-compatible /v1/chat/completions.

We use httpx.AsyncClient directly rather than the OpenAI SDK because:
  1. vLLM's auth is optional (defaults to "EMPTY" api_key).
  2. The OpenAI SDK adds retries/backoff we don't necessarily want against
     a local vLLM server (faster to fail-fast).
  3. Direct HTTP is simpler to reason about for the production path.

count_tokens loads the served model's Hugging Face tokenizer locally
(`tokenizers.Tokenizer.from_pretrained(config.model)`) and encodes with zero
per-call round-trip — the EXACT tokenizer the model uses. The tokenizer.json
is fetched once at construction (then disk-cached). If it can't load (offline,
gated repo, or config.model isn't a Hub repo id), the provider logs a WARNING
once and count_tokens degrades to a labeled `len//4` heuristic so the budget
gate keeps working on an approximation rather than crashing.
"""

import httpx
from tokenizers import Tokenizer

from codeoracle.adapters.llm.config import VLLMProviderConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.llm.tokenizer_loader import (
    count_tokens_with_fallback,
    load_hf_tokenizer,
)
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class VLLMProvider(LLMProvider):
    def __init__(self, config: VLLMProviderConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
            headers={"Authorization": f"Bearer {config.api_key.get_secret_value()}"},
        )
        # Exact local tokenizer for the served model; None if it can't load
        # (load_hf_tokenizer logs the WARNING once). count_tokens falls back to
        # a labeled heuristic when None.
        self._tokenizer: Tokenizer | None = load_hf_tokenizer(config.model)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        if structured_output is not None:
            raise NotImplementedError(
                "VLLMProvider does not implement structured_output. It is part of the "
                "spec §5.7 request-fingerprint contract, and the OpenAIProvider (dev/live "
                "default) implements it; vLLM's native path is the `guided_json` decoding "
                "parameter (the target's JSON schema passed in the request body). DEFERRAL: no "
                "slice consumer routes structured_output through vLLM — the slice runs on "
                "OpenAI. Plan 13 gates WHEN the production vLLM/Qwen path first goes live (its "
                "Deferred block: 'Live-LLM gate provider matrix … Plan 13 decides scope'); "
                "wiring `guided_json` is a PRECONDITION of that go-live, built by whoever "
                "performs it — Plan 13 decides the timing, not the implementation. Failing loud "
                "rather than silently dropping the constraint (a silently-ignored schema would "
                "return unvalidated output)."
            )

        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters_schema,
                    },
                }
                for t in tools
            ]

        try:
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"vLLM HTTP error: {exc}") from exc

        body = response.json()
        choice = body["choices"][0]
        usage = body.get("usage") or {}

        return ChatResponse(
            content=choice["message"].get("content") or "",
            usage=TokenUsage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
            finish_reason=_map_finish_reason(choice.get("finish_reason")),
            tool_calls=[
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments_json=tc["function"]["arguments"],
                )
                for tc in (choice["message"].get("tool_calls") or [])
            ],
        )

    def count_tokens(self, text: str) -> int:
        """Exact count via the served model's HF tokenizer (loaded in __init__).
        Falls back to a labeled len//4 heuristic if the tokenizer didn't load."""
        return count_tokens_with_fallback(self._tokenizer, text)

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain the underlying ``httpx.AsyncClient`` (Plan 10 Task 11 calls in lifespan shutdown)."""
        await self._client.aclose()


def _map_finish_reason(reason: str | None) -> str:
    if reason in ("stop", "length", "tool_calls", "content_filter"):
        return reason
    return "stop"
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/providers/test_vllm.py`**

```python
"""Plan 4 Task 10: VLLMProvider unit tests (respx-mocked)."""

import httpx
import pytest
import respx

from codeoracle.adapters.llm.config import VLLMProviderConfig
from codeoracle.adapters.llm.providers import vllm as vllm_module
from codeoracle.adapters.llm.providers.vllm import VLLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage


@pytest.fixture(autouse=True)
def _no_hub_fetch(monkeypatch):
    """Provider __init__ now calls load_hf_tokenizer(model), which would fetch
    tokenizer.json from the Hub. Patch it to None by default so construction is
    network-free; count_tokens then uses the labeled len//4 fallback. The
    dedicated exact-path test below overrides this with a stub tokenizer."""
    monkeypatch.setattr(vllm_module, "load_hf_tokenizer", lambda model: None)
    yield


def _provider() -> VLLMProvider:
    return VLLMProvider(
        VLLMProviderConfig(base_url="http://vllm-test:8000/v1", model="Qwen/Qwen3.6-32B"),    )


async def test_vllm_chat_happy_path():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://vllm-test:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "cmpl-x",
                    "object": "chat.completion",
                    "model": "Qwen/Qwen3.6-32B",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                },
            )
        )
        provider = _provider()
        response = await provider.chat([ChatMessage(role="user", content="ping")])
        assert response.content == "pong"
        assert response.usage.input_tokens == 4


async def test_vllm_wraps_http_error():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://vllm-test:8000/v1/chat/completions").mock(
            return_value=httpx.Response(503, json={"error": "vllm overloaded"}),
        )
        provider = _provider()
        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat([ChatMessage(role="user", content="ping")])
        assert "vLLM HTTP error" in str(exc_info.value)


async def test_vllm_count_tokens_falls_back_to_heuristic_when_tokenizer_absent():
    """When the HF tokenizer didn't load (the autouse fixture forces None),
    count_tokens uses the labeled len//4 fallback — empty → 0, else len//4."""
    provider = _provider()
    assert provider.count_tokens("") == 0
    assert provider.count_tokens("abcdefgh") == 2  # 8 // 4


async def test_vllm_count_tokens_uses_exact_tokenizer_when_present(monkeypatch):
    """When a tokenizer IS loaded, count_tokens returns its exact encode length
    (here a stub whose Encoding.ids length is deterministic), not the heuristic."""
    class _StubEncoding:
        ids = [0, 1, 2, 3, 4]  # 5 tokens

    class _StubTokenizer:
        def encode(self, text: str):  # mirrors tokenizers.Tokenizer.encode
            return _StubEncoding()

    monkeypatch.setattr(vllm_module, "load_hf_tokenizer", lambda model: _StubTokenizer())
    provider = _provider()
    assert provider.count_tokens("anything") == 5
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/adapters/llm/providers/test_vllm.py -v
uv run ruff check src/codeoracle/adapters/llm/providers/vllm.py
uv run ty check src/codeoracle/adapters/llm/providers/vllm.py
```

Expected: tests PASS; no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/adapters/llm/providers/vllm.py tests/unit/adapters/llm/providers/test_vllm.py
git commit -m "feat: Plan 4 Task 10 — VLLMProvider (httpx + OpenAI-compatible)"
```

---

## Task 11: `src/codeoracle/adapters/llm/providers/ollama.py` — OllamaProvider

**What this task delivers:** the Ollama implementation. Like vLLM, uses `httpx.AsyncClient` directly against `/api/chat` (Ollama's native shape — distinct from OpenAI-compatible). `count_tokens` reuses the same `tokenizer_loader` path as vLLM: it attempts the served model's Hugging Face tokenizer in `__init__` and encodes locally with zero per-call round-trip. NOTE: an Ollama model id is an Ollama **tag** (e.g. `qwen2.5:32b`), not a Hub repo id, so `load_hf_tokenizer` resolves ONLY when the operator sets `CODEORACLE_LLM_OLLAMA__MODEL` to a Hub-style id (e.g. `Qwen/Qwen2.5-32B`); otherwise it logs a WARNING once and `count_tokens` degrades to the labeled `len(text) // 4` heuristic. A tag→Hub mapping table is out of slice scope (it would need per-tag curation). This is strictly better than the prior unconditional word-split: it is exact whenever a Hub id is configured, and a clearly-labeled approximation otherwise.

**Files:**
- Create: `src/codeoracle/adapters/llm/providers/ollama.py`
- Create: `tests/unit/adapters/llm/providers/test_ollama.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/providers/ollama.py`**

```python
"""OllamaProvider — Ollama's native /api/chat shape.

Like VLLMProvider, uses httpx directly. Ollama's `/api/chat` returns
{"message":{"content":"..."},"done":bool,"eval_count":int,"prompt_eval_count":int}.
Tool calling support depends on the model — Plan 4 includes it in the
request when tools is non-empty but doesn't validate model capability.

count_tokens uses the shared tokenizer_loader: it attempts the served model's
Hugging Face tokenizer in __init__ and encodes locally. An Ollama model id is a
TAG (e.g. "qwen2.5:32b"), not a Hub repo id, so the tokenizer resolves only when
CODEORACLE_LLM_OLLAMA__MODEL is set to a Hub-style id; otherwise load_hf_tokenizer logs
a WARNING once and count_tokens degrades to a labeled len//4 heuristic.
"""

import json

import httpx
from tokenizers import Tokenizer

from codeoracle.adapters.llm.config import OllamaProviderConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.llm.tokenizer_loader import (
    count_tokens_with_fallback,
    load_hf_tokenizer,
)
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class OllamaProvider(LLMProvider):
    def __init__(self, config: OllamaProviderConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=5.0),
        )
        # Exact local tokenizer if config.model resolves to a Hub repo id;
        # None otherwise (load_hf_tokenizer logs the WARNING once). See the
        # module docstring re: Ollama tags vs Hub ids.
        self._tokenizer: Tokenizer | None = load_hf_tokenizer(config.model)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        if structured_output is not None:
            raise NotImplementedError(
                "OllamaProvider does not implement structured_output. It is part of the "
                "spec §5.7 request-fingerprint contract, and the OpenAIProvider (dev/live "
                "default) implements it; Ollama's native path is the `format` request field "
                "(set to the target's JSON schema). DEFERRAL: Ollama is a local-dev convenience "
                "provider with no slice consumer routing structured_output through it — the "
                "slice runs on OpenAI. Plan 13 gates WHEN the non-OpenAI provider matrix widens "
                "(its Deferred block: 'Live-LLM gate provider matrix … Plan 13 decides scope'); "
                "setting the `format` schema is a PRECONDITION of any Ollama go-live, built by "
                "whoever performs it — Plan 13 decides the timing, not the implementation. "
                "Failing loud rather than silently dropping the constraint (a silently-ignored "
                "schema would return unvalidated output)."
            )

        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters_schema,
                    },
                }
                for t in tools
            ]

        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Ollama HTTP error: {exc}") from exc

        body = response.json()
        message = body.get("message") or {}
        return ChatResponse(
            content=message.get("content") or "",
            usage=TokenUsage(
                input_tokens=body.get("prompt_eval_count", 0),
                output_tokens=body.get("eval_count", 0),
            ),
            finish_reason="stop" if body.get("done") else "length",
            tool_calls=[
                ToolCall(
                    id=tc.get("id") or f"call_{i}",
                    name=tc["function"]["name"],
                    arguments_json=json.dumps(tc["function"].get("arguments", {})),
                )
                for i, tc in enumerate(message.get("tool_calls") or [])
            ],
        )

    def count_tokens(self, text: str) -> int:
        """Exact count via the served model's HF tokenizer when config.model is a
        Hub repo id (loaded in __init__); otherwise the labeled len//4 fallback.
        Same path as vLLM (see tokenizer_loader)."""
        return count_tokens_with_fallback(self._tokenizer, text)

    def model_name(self) -> str:
        return self._config.model

    def max_context_tokens(self) -> int:
        return self._config.max_context_tokens

    async def aclose(self) -> None:
        """Drain the underlying ``httpx.AsyncClient`` (Plan 10 Task 11 calls in lifespan shutdown)."""
        await self._client.aclose()
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/providers/test_ollama.py`**

```python
"""Plan 4 Task 11: OllamaProvider unit tests (respx-mocked)."""

import httpx
import pytest
import respx

from codeoracle.adapters.llm.config import OllamaProviderConfig
from codeoracle.adapters.llm.providers import ollama as ollama_module
from codeoracle.adapters.llm.providers.ollama import OllamaProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage


@pytest.fixture(autouse=True)
def _no_hub_fetch(monkeypatch):
    """Provider __init__ calls load_hf_tokenizer(model); an Ollama tag is not a
    Hub id, so this would attempt (and fail) a Hub fetch. Patch it to None so
    construction is network-free; count_tokens then uses the len//4 fallback."""
    monkeypatch.setattr(ollama_module, "load_hf_tokenizer", lambda model: None)
    yield


def _provider() -> OllamaProvider:
    return OllamaProvider(
        OllamaProviderConfig(base_url="http://ollama-test:11434", model="qwen2.5:32b"),    )


async def test_ollama_chat_happy_path():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://ollama-test:11434/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "qwen2.5:32b",
                    "message": {"role": "assistant", "content": "pong"},
                    "done": True,
                    "prompt_eval_count": 4,
                    "eval_count": 1,
                },
            )
        )
        provider = _provider()
        response = await provider.chat([ChatMessage(role="user", content="ping")])
        assert response.content == "pong"
        assert response.usage.input_tokens == 4
        assert response.usage.output_tokens == 1
        assert response.finish_reason == "stop"


async def test_ollama_wraps_http_error():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://ollama-test:11434/api/chat").mock(
            return_value=httpx.Response(500, json={"error": "model not loaded"}),
        )
        provider = _provider()
        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat([ChatMessage(role="user", content="ping")])
        assert "Ollama HTTP error" in str(exc_info.value)


async def test_ollama_not_done_maps_to_length():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://ollama-test:11434/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "qwen2.5:32b",
                    "message": {"role": "assistant", "content": "partial"},
                    "done": False,
                    "prompt_eval_count": 4,
                    "eval_count": 100,
                },
            )
        )
        provider = _provider()
        response = await provider.chat([ChatMessage(role="user", content="generate at length")])
        assert response.finish_reason == "length"


async def test_ollama_count_tokens_falls_back_to_heuristic_for_ollama_tag():
    """An Ollama tag isn't a Hub id (autouse fixture forces the loader to None),
    so count_tokens uses the labeled len//4 fallback — empty → 0, else len//4."""
    provider = _provider()
    assert provider.count_tokens("") == 0
    assert provider.count_tokens("abcdefgh") == 2  # 8 // 4
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/adapters/llm/providers/test_ollama.py -v
uv run ruff check src/codeoracle/adapters/llm/providers/ollama.py
uv run ty check src/codeoracle/adapters/llm/providers/ollama.py
```

Expected: tests PASS; no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/adapters/llm/providers/ollama.py tests/unit/adapters/llm/providers/test_ollama.py
git commit -m "feat: Plan 4 Task 11 — OllamaProvider"
```

---

## Task 12: `src/codeoracle/adapters/llm/factory.py` — LLMProviderFactory

**What this task delivers:** the RAW provider constructor for `LLMConfig.provider`, with provider modules imported lazily inside `.create()` so importing the factory doesn't drag every SDK into memory. Budget-wrapping is the next task (`BudgetedLLMProvider`, Task 12b) — this factory is the unbounded inner that `build_budgeted_provider` wraps; it is never called directly by a composition root.

**Files:**
- Create: `src/codeoracle/adapters/llm/factory.py`
- Create: `tests/unit/adapters/llm/test_factory.py`

- [ ] **Step 1: Write `src/codeoracle/adapters/llm/factory.py`**

```python
"""LLMProviderFactory — constructs the RAW provider for LLMConfig.provider.

NOT the blessed construction path: a bare factory product is unbounded.
Production code builds providers through
`codeoracle.services.llm.build_budgeted_provider`, which wraps this factory's
output in BudgetedLLMProvider (services/llm/) before any composition root sees
it. NEVER instantiated inside a service — services receive a constructed,
budget-wrapped LLMProvider via DI.

Provider modules are imported lazily inside .create() so:
  - import codeoracle.adapters.llm.factory  (cheap; no SDK imports yet)
  - factory.create()  (imports only the selected provider's module)

This keeps cold-start cost down for processes that don't all need every SDK.
"""

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.interfaces import LLMProvider


class LLMProviderFactory:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def create(self) -> LLMProvider:
        """Construct the provider matching `LLMConfig.provider`.

        Each branch imports lazily — see module docstring for rationale.

        The `if X is None: raise` form is used instead of `assert X is not None`
        because asserts are stripped under `python -O`, and the None-check is
        load-bearing (it's the second line of defense after LLMConfig's
        cross-validator; ty also narrows correctly with explicit checks).
        """
        match self._config.provider:
            case "openai":
                from codeoracle.adapters.llm.providers.openai import OpenAIProvider
                if self._config.openai is None:
                    raise ValueError(
                        "LLMConfig.openai is None despite provider='openai' — "
                        "the cross-validator should have caught this."
                    )
                return OpenAIProvider(self._config.openai)
            case "anthropic":
                from codeoracle.adapters.llm.providers.anthropic import AnthropicProvider
                if self._config.anthropic is None:
                    raise ValueError(
                        "LLMConfig.anthropic is None despite provider='anthropic'."
                    )
                return AnthropicProvider(self._config.anthropic)
            case "vllm":
                from codeoracle.adapters.llm.providers.vllm import VLLMProvider
                if self._config.vllm is None:
                    raise ValueError(
                        "LLMConfig.vllm is None despite provider='vllm'."
                    )
                return VLLMProvider(self._config.vllm)
            case "ollama":
                from codeoracle.adapters.llm.providers.ollama import OllamaProvider
                if self._config.ollama is None:
                    raise ValueError(
                        "LLMConfig.ollama is None despite provider='ollama'."
                    )
                return OllamaProvider(self._config.ollama)
            case "mock":
                from codeoracle.adapters.llm.providers.mock import MockLLMProvider
                if self._config.mock is None:
                    raise ValueError(
                        "LLMConfig.mock is None despite provider='mock'."
                    )
                return MockLLMProvider(self._config.mock)
            case _:
                # Defensive: `ProviderName` is a `Literal[...]` so the
                # cross-validator should keep us inside the five known cases
                # at config-load time. If someone extends `ProviderName`
                # without adding a case branch above, this raises loudly
                # rather than falling off the end and returning `None`.
                # Also makes the match exhaustive for `ty`.
                raise ValueError(
                    f"Unsupported provider {self._config.provider!r}. "
                    "Extend ProviderName AND add a case branch in "
                    "LLMProviderFactory.create() together."
                )
```

- [ ] **Step 2: Write `tests/unit/adapters/llm/test_factory.py`**

```python
"""Plan 4 Task 12: LLMProviderFactory unit tests."""

from codeoracle.adapters.llm.config import (
    AnthropicProviderConfig,
    LLMConfig,
    MockLLMProviderConfig,
    OllamaProviderConfig,
    OpenAIProviderConfig,
    VLLMProviderConfig,
)
from codeoracle.adapters.llm.factory import LLMProviderFactory
from codeoracle.adapters.llm.providers.anthropic import AnthropicProvider
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.adapters.llm.providers.ollama import OllamaProvider
from codeoracle.adapters.llm.providers.openai import OpenAIProvider
from codeoracle.adapters.llm.providers.vllm import VLLMProvider


def test_factory_creates_openai():
    cfg = LLMConfig(
        provider="openai",
        openai=OpenAIProviderConfig(api_key="sk-x"),
    )
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, OpenAIProvider)


def test_factory_creates_anthropic():
    cfg = LLMConfig(
        provider="anthropic",
        anthropic=AnthropicProviderConfig(api_key="sk-ant-x"),
    )
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, AnthropicProvider)


def test_factory_creates_vllm():
    cfg = LLMConfig(provider="vllm", vllm=VLLMProviderConfig())
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, VLLMProvider)


def test_factory_creates_ollama():
    cfg = LLMConfig(provider="ollama", ollama=OllamaProviderConfig())
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, OllamaProvider)


def test_factory_creates_mock(tmp_path):
    cfg = LLMConfig(provider="mock", mock=MockLLMProviderConfig(scripts_dir=str(tmp_path)))
    provider = LLMProviderFactory(cfg).create()
    assert isinstance(provider, MockLLMProvider)
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/adapters/llm/test_factory.py -v
uv run ruff check src/codeoracle/adapters/llm/factory.py
uv run ty check src/codeoracle/adapters/llm/factory.py
```

Expected: 5 tests PASS; no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/adapters/llm/factory.py tests/unit/adapters/llm/test_factory.py
git commit -m "feat: Plan 4 Task 12 — LLMProviderFactory (lazy provider imports)"
```

---

## Task 12b: `services/llm/budgeted_provider.py` — BudgetedLLMProvider decorator + blessed construction path

**What this task delivers:** budget enforcement as a service-layer *decorator* (NFR D7a-1), not an upward import from `adapters/`. `BudgetedLLMProvider` wraps any raw `LLMProvider` and runs `ContextBudgetService.check()` before each `chat()`; `build_budgeted_provider(config, budget)` is the single blessed construction path (factory → wrap), so no composition root can hold an unbounded provider. Raw providers under `adapters/llm/providers/` stay free of any `services/` import — keeping `adapters/llm/` a leaf layer.

**Files:**
- Create: `src/codeoracle/services/llm/__init__.py`
- Create: `src/codeoracle/services/llm/budgeted_provider.py`
- Create: `tests/unit/services/llm/test_budgeted_provider.py`

- [ ] **Step 1: Write `src/codeoracle/services/llm/budgeted_provider.py`**

```python
"""BudgetedLLMProvider — service-layer budget enforcement composed around a raw provider.

Raw providers (adapters/llm/providers/) are pure I/O: they hold no reference to
the platform's token policy. ContextBudgetService is a domain rule (services/),
so the budget is enforced by WRAPPING a provider at the service layer — never by
an upward import from adapters/ into services/ (NFR D7a-1, memory
`strict-layer-purity`). `build_budgeted_provider` is the single blessed
construction path; composition roots (workers/_base/wiring.py, the agent's
FastAPI lifespan) call it once.
"""

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.factory import LLMProviderFactory
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition
from codeoracle.services.context_budget import ContextBudgetService


class BudgetedLLMProvider(LLMProvider):
    """Wraps a raw LLMProvider, enforcing ContextBudgetService.check() before
    each chat() delegates to the inner provider. Every other LLMProvider method
    delegates unchanged."""

    def __init__(self, inner: LLMProvider, budget: ContextBudgetService) -> None:
        self._inner = inner
        self._budget = budget

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse:
        # Enforce the platform token cap before the inner provider's network
        # call, counting with the inner provider's OWN tokenizer (each model
        # counts differently). Raises ContextBudgetExceeded.
        self._budget.check(messages, self._inner.count_tokens)
        return await self._inner.chat(
            messages, tools=tools, structured_output=structured_output
        )

    def count_tokens(self, text: str) -> int:
        return self._inner.count_tokens(text)

    def model_name(self) -> str:
        return self._inner.model_name()

    def max_context_tokens(self) -> int:
        return self._inner.max_context_tokens()

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_budgeted_provider(
    config: LLMConfig, budget: ContextBudgetService
) -> LLMProvider:
    """The single blessed path to a production LLM provider: construct the raw
    provider via LLMProviderFactory, then wrap it in BudgetedLLMProvider so no
    composition root can accidentally hold an unbounded provider (fail-safe)."""
    return BudgetedLLMProvider(LLMProviderFactory(config).create(), budget)
```

- [ ] **Step 2: Write `src/codeoracle/services/llm/__init__.py`**

```python
from codeoracle.services.llm.budgeted_provider import (
    BudgetedLLMProvider,
    build_budgeted_provider,
)

__all__ = ["BudgetedLLMProvider", "build_budgeted_provider"]
```

- [ ] **Step 3: Write `tests/unit/services/llm/test_budgeted_provider.py`**

```python
"""Plan 4 Task 12b: BudgetedLLMProvider decorator unit tests.

One decorator test pair replaces the five per-provider
test_*_respects_context_budget tests removed in NFR D7a-1 — budget enforcement
now lives in ONE place, so it is tested in ONE place.
"""

import pytest

from codeoracle.adapters.llm.config import MockLLMProviderConfig
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.models.llm import ChatMessage
from codeoracle.services.context_budget import ContextBudgetExceeded, ContextBudgetService
from codeoracle.services.llm import BudgetedLLMProvider


def _mock(tmp_path, *, strict: bool) -> MockLLMProvider:
    return MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=strict)
    )


async def test_budgeted_provider_enforces_budget_before_delegating(tmp_path):
    """check() fires BEFORE inner.chat(): an over-budget request never reaches
    the wrapped provider. The mock counts len(text)//4 tokens, so a message far
    longer than four characters exceeds a 1-token cap."""
    wrapped = BudgetedLLMProvider(
        _mock(tmp_path, strict=True), ContextBudgetService(max_tokens=1)
    )
    with pytest.raises(ContextBudgetExceeded):
        await wrapped.chat(
            [ChatMessage(role="user", content="far longer than one token")]
        )


async def test_budgeted_provider_delegates_within_budget(tmp_path):
    """Within budget, chat() reaches the inner provider (a non-strict mock
    returns an empty ChatResponse for an unknown fingerprint), and the other
    LLMProvider methods delegate unchanged."""
    inner = _mock(tmp_path, strict=False)
    wrapped = BudgetedLLMProvider(inner, ContextBudgetService(max_tokens=24_000))
    response = await wrapped.chat([ChatMessage(role="user", content="ping")])
    assert response.content == ""
    assert wrapped.model_name() == inner.model_name() == "mock"
    assert wrapped.count_tokens("12345678") == 2  # mock stub: len(text) // 4
    await wrapped.aclose()  # delegates to the inner no-op; must not raise
```

- [ ] **Step 4: Run + lint + type**

```bash
uv run pytest tests/unit/services/llm/test_budgeted_provider.py -v
uv run ruff check src/codeoracle/services/llm/
uv run ty check src/codeoracle/services/llm/
```

Expected: 2 tests PASS; no lint/type errors. After this task, no module under `adapters/llm/providers/` imports `codeoracle.services` any longer — the backwards layer edge NFR D7a-1 flagged is gone.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/services/llm/ tests/unit/services/llm/test_budgeted_provider.py
git commit -m "feat: Plan 4 Task 12b — BudgetedLLMProvider decorator (services/llm); budget enforcement composed, not inherited"
```

---

## Task 13: Banned-API enforcement test

**What this task delivers:** the positive-and-negative test that proves ruff's `flake8-tidy-imports.banned-api` rule actually fires (forbidden import in services/ fails) AND the per-file-ignore exempts providers/ (allowed import in providers/ passes).

**Files:**
- Create: `tests/unit/test_banned_api_enforcement.py`

- [ ] **Step 1: Write `tests/unit/test_banned_api_enforcement.py`**

```python
"""Plan 4 Task 13: Prove the structural-enforcement contract works.

If this test fails, the ruff configuration in pyproject.toml has drifted
and the platform's "no direct LLM SDK imports outside providers/"
guarantee is broken — meaning Plan 4's Plan 5+ inheritance contract no
longer holds.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff(target_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-m", "ruff", "check",
            "--config", str(REPO_ROOT / "pyproject.toml"),
            "--select", "TID251",
            str(target_file),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


@pytest.mark.parametrize("sdk_name", ["openai", "anthropic", "litellm", "ollama"])
def test_banned_api_blocks_direct_import_outside_providers(tmp_path, sdk_name):
    """A file outside providers/ that imports a banned SDK must fail ruff."""
    bad_file = tmp_path / "fake_service.py"
    bad_file.write_text(f"import {sdk_name}\n")

    result = _run_ruff(bad_file)

    assert result.returncode != 0, (
        f"ruff did NOT flag 'import {sdk_name}' in a non-providers file. "
        f"banned-api enforcement is broken.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert sdk_name in result.stdout or sdk_name in result.stderr


def test_banned_api_allows_import_in_providers_file():
    """The per-file-ignore for src/codeoracle/adapters/llm/providers/*.py must work.
    We point ruff at the real openai.py (which contains `import openai`)
    and expect zero TID251 violations."""
    target = REPO_ROOT / "src" / "codeoracle" / "adapters" / "llm" / "providers" / "openai.py"
    assert target.exists(), f"Plan 4 Task 8 not yet applied: {target} missing"

    result = _run_ruff(target)
    assert result.returncode == 0, (
        f"ruff flagged the openai SDK import inside providers/openai.py — "
        f"per-file-ignore for src/codeoracle/adapters/llm/providers/*.py is broken.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_banned_api_message_includes_actionable_redirect(tmp_path):
    """The ruff message should tell the developer where to import instead.
    This is a documentation property — broken message = developers
    re-discover the convention from scratch."""
    bad_file = tmp_path / "fake_service.py"
    bad_file.write_text("import openai\n")

    result = _run_ruff(bad_file)
    assert "codeoracle.adapters.llm" in (result.stdout + result.stderr), (
        "banned-api message doesn't mention codeoracle.adapters.llm — "
        "developers won't know where to import from"
    )
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/unit/test_banned_api_enforcement.py -v
```

Expected: all 6 tests PASS (1 parametrized × 4 SDKs + 2 standalone = 6).

- [ ] **Step 3: Lint**

```bash
uv run ruff check tests/unit/test_banned_api_enforcement.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_banned_api_enforcement.py
git commit -m "feat: Plan 4 Task 13 — banned-API ruff enforcement test"
```

---

## Task 14: Live-LLM gate — CI workflow + smoke test

**What this task delivers:** the GitHub Actions workflow that runs `pytest -m live_llm` only when a PR has the `run-live-llm` label (or on a nightly schedule) + the single smoke test that proves the workflow + OpenAIProvider work end-to-end against the real OpenAI API. Plan 13 adds the 9 slice integration tests under the same marker.

**Files:**
- Create: `.github/workflows/test-live-llm.yml`
- Create: `tests/integration/llm/__init__.py` (empty)
- Create: `tests/integration/llm/test_openai_live_roundtrip.py`

- [ ] **Step 1: Write `.github/workflows/test-live-llm.yml`**

```yaml
name: Live-LLM Tests

# Triggers:
#  - PR labeled "run-live-llm" — manual, ad-hoc validation
#  - Nightly schedule — catches provider drift even when no one labels
#  - workflow_dispatch — humans can fire it from the Actions tab
on:
  pull_request:
    types: [labeled]
  schedule:
    # 03:00 UTC nightly — outside US business hours, before EU morning
    - cron: '0 3 * * *'
  workflow_dispatch:

jobs:
  live-llm-smoke:
    # Only run on label events when the label is exactly "run-live-llm"
    if: |
      (github.event_name == 'pull_request' && github.event.label.name == 'run-live-llm') ||
      github.event_name == 'schedule' ||
      github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    # Spec §5.7 declares this gate ADVISORY: live-LLM tests prove the
    # platform's contract against the real provider, but their failure
    # modes (API outage, quota exhaustion, model deprecation announced
    # mid-PR) are external to the change under review. continue-on-error
    # lets the job surface failures in the Actions UI without blocking
    # merge — paired with NOT marking this workflow as a required check
    # in branch protection. A failed run becomes a notification, not a
    # gate; promotion to "required" is an explicit branch-protection
    # change a human makes, not a CI default.
    continue-on-error: true

    env:
      # CODEORACLE_LLM_OPENAI__API_KEY is what LLMConfig reads — keep ONE canonical env
      # var rather than two (avoids "which one does the platform read?"
      # confusion). The `OPENAI_API_KEY` secret in repo settings is mapped
      # directly to CODEORACLE_LLM_OPENAI__API_KEY; we don't also expose it under its
      # native name.
      CODEORACLE_LLM_PROVIDER: openai
      CODEORACLE_LLM_OPENAI__API_KEY: ${{ secrets.OPENAI_API_KEY }}
      # CODEORACLE_EMBEDDING__* — required by Plan 3 Settings() fail-fast. Plan 4's
      # smoke test path (LLMConfig → factory → OpenAIProvider.chat) doesn't
      # touch Settings(), so these are inert TODAY but pre-seeded so the
      # workflow file stays stable when Plan 5 adds embedding live-tests
      # against the same gate (no Plan 5 commit needs to touch this file).
      CODEORACLE_EMBEDDING__MODEL: text-embedding-3-small
      CODEORACLE_EMBEDDING__DIM: '1536'

    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: latest
      - run: uv sync --frozen
      - name: Verify live-LLM credentials present
        run: |
          if [ -z "$CODEORACLE_LLM_OPENAI__API_KEY" ]; then
            echo "ERROR: OPENAI_API_KEY secret not set in repo Settings."
            echo "Live-LLM workflow cannot run without it."
            exit 1
          fi
      - name: Run live-LLM smoke tests
        run: uv run pytest -m live_llm tests/integration/llm/ -v
```

- [ ] **Step 2: Write `tests/integration/llm/__init__.py` (empty)**

```bash
mkdir -p tests/integration/llm && touch tests/integration/llm/__init__.py
```

- [ ] **Step 3: Write `tests/integration/llm/test_openai_live_roundtrip.py`**

```python
"""Plan 4 Task 14: live-LLM smoke test.

Runs ONLY in the test-live-llm GitHub Actions workflow (PR-labeled, nightly,
or manual dispatch). Asserts that:
  1. OpenAIProvider can be constructed from env-injected config.
  2. A chat() round-trip to the real OpenAI API succeeds.
  3. The response shape matches our ChatResponse model.
  4. Token usage is non-zero (proves the API actually responded).

If this test fails:
  - Check OPENAI_API_KEY is valid and has credit.
  - Check CODEORACLE_LLM_OPENAI__MODEL is a real model name.
  - Check OpenAI's API isn't returning a new finish_reason we don't map.

Plan 13 adds the 9 vertical-slice integration tests under the same live_llm
marker.
"""

import os

import pytest

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.models.llm import ChatMessage
from codeoracle.services.context_budget import ContextBudgetService
from codeoracle.services.llm import build_budgeted_provider

pytestmark = pytest.mark.live_llm


@pytest.fixture(scope="session")
def openai_provider():
    """Construct OpenAIProvider from env. Skip when CODEORACLE_LLM_OPENAI__API_KEY is
    unset — protects local dev runs that accidentally include this marker.

    LLMConfig reads `CODEORACLE_LLM_OPENAI__API_KEY` (the platform-canonical name).
    We do not bridge from native `OPENAI_API_KEY` — keeping a single canonical
    env var avoids "which one does the platform read?" confusion. Developers
    running this locally must set `CODEORACLE_LLM_OPENAI__API_KEY` (the same name CI
    uses).
    """
    if not os.environ.get("CODEORACLE_LLM_OPENAI__API_KEY"):
        pytest.skip(
            "CODEORACLE_LLM_OPENAI__API_KEY not set — set it to a valid OpenAI key to "
            "run live-LLM tests locally (CI sets this from the OPENAI_API_KEY secret)."
        )
    os.environ.setdefault("CODEORACLE_LLM_PROVIDER", "openai")

    config = LLMConfig()
    budget = ContextBudgetService(max_tokens=24_000)
    return build_budgeted_provider(config, budget)


async def test_openai_live_pong_roundtrip(openai_provider):
    """Smoke test: ask for a literal string back, assert it's in the response."""
    messages = [
        ChatMessage(
            role="user",
            content="Reply with the literal string 'pong' and nothing else.",
            template_id="live_smoke_ping_v1",
        )
    ]
    response = await openai_provider.chat(messages)

    assert "pong" in response.content.lower(), (
        f"OpenAI didn't return the expected token. content={response.content!r}"
    )
    assert response.usage.input_tokens > 0, "input_tokens should be non-zero"
    assert response.usage.output_tokens > 0, "output_tokens should be non-zero"
    assert response.finish_reason in ("stop", "length"), (
        f"unexpected finish_reason: {response.finish_reason}"
    )
```

- [ ] **Step 4: Lint the new test file**

```bash
uv run ruff check tests/integration/llm/test_openai_live_roundtrip.py
```

Expected: lints clean. (YAML files are not linted here — ruff doesn't lint
YAML and we haven't added `actionlint` yet; defer to Plan 5/6 if needed.)

- [ ] **Step 5: Verify the smoke test is collected under the marker**

```bash
uv run pytest --collect-only -m live_llm tests/integration/llm/ 2>&1 | grep test_openai_live_pong_roundtrip
```

Expected: prints the test name. If it doesn't, the marker registration in Plan 4 Task 1 Step 6 didn't land — re-check pyproject.toml.

- [ ] **Step 6: Confirm the test is SKIPPED locally (no API key set)**

```bash
unset CODEORACLE_LLM_OPENAI__API_KEY
uv run pytest -m live_llm tests/integration/llm/ -v
```

Expected: the test reports SKIPPED (because the env-key check skips it). It must NOT fail with "couldn't load Settings" or similar — the skip is graceful.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/test-live-llm.yml \
        tests/integration/llm/__init__.py \
        tests/integration/llm/test_openai_live_roundtrip.py
git commit -m "feat: Plan 4 Task 14 — live-LLM CI gate + OpenAI smoke test"
```

---

## Task 15: Final verification + push

**What this task delivers:** the all-up gate before Plan 5 inherits the LLM facade. Runs the entire test suite, lint, type-check, and (offline) collects the live-LLM marker to prove it's wired correctly. Updates `.env.example`. Pushes.

**Files:**
- Modify: `.env.example` (un-comment CODEORACLE_LLM_ block)

- [ ] **Step 1: Update `.env.example`**

Open `.env.example` (Plan 3 Task 7 Step 2) and replace the LLM placeholder block:

```bash
# LLM provider — Plan 4 wires these. provider is one of: openai, anthropic, vllm, ollama, mock.
# In dev, OpenAI is the default (memory project-dev-llm-provider-openai). Replace api_key
# with your real key before running anything that calls chat().
CODEORACLE_LLM_PROVIDER=openai
CODEORACLE_LLM_OPENAI__API_KEY=sk-replace-me
# model defaults from pinned.env OPENAI_DEV_MODEL — uncomment to override
# CODEORACLE_LLM_OPENAI__MODEL=gpt-4o-mini
# CODEORACLE_LLM_OPENAI__MAX_CONTEXT_TOKENS=128000

# For anthropic, vllm, ollama, mock — uncomment the relevant block and switch CODEORACLE_LLM_PROVIDER.
# Mock is test-only; do not use in dev.
# CODEORACLE_LLM_ANTHROPIC__API_KEY=sk-ant-replace-me
# CODEORACLE_LLM_VLLM__BASE_URL=http://localhost:8000/v1
# CODEORACLE_LLM_OLLAMA__BASE_URL=http://localhost:11434
# CODEORACLE_LLM_MOCK__SCRIPTS_DIR=tests/fixtures/llm_scripts
# CODEORACLE_LLM_MOCK__STRICT=true
```

- [ ] **Step 2: Run the full unit suite**

```bash
uv run pytest tests/unit/ -v
```

Expected: every test in Plan 3 + Plan 4 PASSES.

- [ ] **Step 3: Confirm live-LLM tests are SKIPPED (no key)**

```bash
unset CODEORACLE_LLM_OPENAI__API_KEY
uv run pytest tests/integration/llm/ -v
```

Expected: live-LLM test SKIPS gracefully (does not error on missing Settings).

- [ ] **Step 4: Run full lint + type**

```bash
uv run ruff check src/ tests/
uv run ty check src/codeoracle/
```

Expected: no errors.

- [ ] **Step 5: Confirm banned-API rule actually fires in a non-providers location**

```bash
# Sanity: write a temp file with a banned import OUTSIDE providers/ and check ruff fails
echo "import openai" > /tmp/fake_check.py
uv run ruff check --select=TID251 /tmp/fake_check.py && echo "REGRESSION: ruff did not flag" || echo "OK — ruff correctly flagged"
rm /tmp/fake_check.py
```

Expected: prints `OK — ruff correctly flagged`.

- [ ] **Step 6: Commit + push**

```bash
git add .env.example
git commit -m "chore: Plan 4 Task 15 — .env.example LLM block + final verification"
git push origin main
```

- [ ] **Step 7: Verify the live-LLM workflow appears in GitHub Actions**

After push, navigate to the repository's Actions tab and confirm `Live-LLM Tests` appears in the workflow list. It won't run yet (no PR with the label, no schedule fire yet), but it must be registered.

If it doesn't appear: the YAML has a syntax error. Run `actionlint .github/workflows/test-live-llm.yml` if available, or paste into the GitHub workflow editor's preview pane.

---

## What Plan 1 inherits from Plan 4

Plan 1 ("Repo skeleton + CI/CD") inherits from Plan 4:

- `.github/workflows/test-live-llm.yml` — the label-gated live-LLM CI job (runs against real OpenAI when the `run-live-llm` PR label is present). Plan 4 Task 8 ships the workflow; Plan 1's CI skeleton is the precondition that registers it. FIX_HINT (round-2 coordinator note): kind-of-edge reattribution candidate — this edge may instead be framed as a Plan-1 forward-promise hook that Plan 4 fills, in which case the bullet becomes a non-load-bearing note rather than a load-bearing provides-to.
- Live-LLM secret-handling tier — Plan 4 owns the convention for injecting `OPENAI_API_KEY` into the live-LLM job via GitHub Secrets gated by environment protection (Plan 4 Task 6/8 establishes the tier; Plan 1's repo-secret scaffolding is the precondition). FIX_HINT (round-2 coordinator note): same kind-of-edge reattribution signal as the test-live-llm row above — coordinator may downgrade to a forward-promise note in round-3.

## What Plan 4 inherits from Plan 1

Plan 4 ("LLM provider abstraction") imports from Plan 1:

- `per-file-ignores` TID251 escape-hatch glob `src/codeoracle/adapters/llm/providers/*.py` — Plan 1 Task 4 Step 3 ships the single glob (a hand-enumerated per-provider list is explicitly rejected — it rots as providers are added); Plan 4 Task 1 Step 5 only *verifies* the glob is intact and contributes zero new entries. The glob exempts every file under `providers/` (incl. `mock.py`) by construction, so direct `import openai|anthropic|litellm|ollama` is permitted ONLY inside `providers/`. Plan 1's glob is the precondition; Plan 4 authors no ruff config.
- `src/codeoracle/adapters/llm/` and `src/codeoracle/adapters/llm/providers/` package directories — empty directories created by Plan 1's repo-skeleton task; Plan 4 Tasks 1-7 populate them with the LLMProvider ABC, factory, MockLLMProvider, four real-provider stubs, fingerprint helper, and tests. Mirrors Plan 5's explicit "repo skeleton" inherit at `plan-5.imports.1`.

## What Plan 1A inherits from Plan 4

Plan 1A ("Operational infrastructure") inherits from Plan 4:

- The DEFERRED: live-LLM secret-handling tier — Plan 1A's Task structure defers the live-LLM tier of secret handling to Plan 4 (per Plan 1A L3259: "live-llm tier is Plan 4"). Plan 4 Task 8 lands the GitHub Secrets wiring (`OPENAI_API_KEY` injected via repository secret gated by environment protection); Plan 1A's secret-handling enumeration is the framework that Plan 4's live-LLM tier slots into.

## What Plan 4 inherits from Plan 1A

Plan 4 ("LLM provider abstraction") imports from Plan 1A:

- `CODEORACLE_LLM_PROVIDER` environment variable convention (defined in `infra/config/defaults.env`) — Plan 4's `LLMProviderFactory` reads this env var to select the concrete provider (`openai` default per memory `project-dev-llm-provider-openai`). Plan 1A owns the env-var declaration and the `infra/config/defaults.env` file; Plan 4 is the downstream consumer.
- `adapters/llm/factory.py` env-consumer routing pattern — Plan 4's factory implements the env-driven provider-selection routing per Plan 1A L586/L639/L678. Plan 1A defines the routing semantics ("read `CODEORACLE_LLM_PROVIDER`; if `openai`, instantiate OpenAIProvider; if `anthropic`, AnthropicProvider; etc."); Plan 4 implements them in `factory.py`.

## What Plan 5 inherits from Plan 4

Plan 5 ("Extractor adapters + indexer workers") starts with:

- `src/codeoracle/adapters/llm/` — the full LLM facade. Workers that need LLM calls (e.g., a future `services/agent.py` invoked from a worker) inject `LLMProviderFactory` at composition time.
- `src/codeoracle/services/context_budget.py` — Workers construct their own `ContextBudgetService` instance in `workers/_base/wiring.py` (NOT shared with the API process; per spec §5.5 line 690).
- The banned-API ruff rule — Plan 5 must not bypass it. Any extractor that calls an LLM goes through the factory.
- The live-LLM workflow — Plan 5 adds NO new tests under this marker (extractors don't talk to LLMs; embeddings are a different adapter contract).
- `LLMConfig` (`src/codeoracle/adapters/llm/config.py`) — Plan 4 Task 3 ships the per-provider Pydantic config model (per-provider fields drawn from `api_key`, `model`, `base_url`, `max_context_tokens`; Anthropic also carries `max_output_tokens`). Plan 5 workers receive `LLMConfig` via `LLMProviderFactory` injection in `workers/_base/wiring.py`. Per FIX_HINT (round-2 coordinator note): this bullet may instead be subsumed under the `src/codeoracle/adapters/llm/` facade row above (plan-4 L3806) if coordinator prefers the umbrella over per-identifier enumeration.
- `ContextBudgetService` class (the Python class itself, distinct from the module file already named at L3807) — Plan 4 Task 4 ships the class in `src/codeoracle/services/context_budget.py`. Plan 5 workers construct their own `ContextBudgetService` instance in `workers/_base/wiring.py` (NOT shared with the API process per spec §5.5 line 690). Umbrella-split per round-2: the L3807 bullet names the file; this bullet names the class so plan-5.imports.26 enumerates against the class identifier explicitly.
- `LLMProviderError` exception class (`src/codeoracle/core/exceptions.py`) — Plan 4 Task 1 ships this exception as the base for all LLM-provider failures, inheriting from `CodeOracleError`. Plan 5 extends the worker-pipeline error hierarchy from `LLMProviderError + CodeOracleError` (worker-side wrapping for retry/dead-letter semantics).
- `src/codeoracle/services/__init__.py` + `src/codeoracle/services/context_budget.py` (Plan 4-created package scaffold) — Plan 4 creates the `services/` subpackage with `__init__.py` and the first service module; Plan 5 inherits this subpackage along with the `indexing/` subpackage Plan 5 itself adds. The scaffold inheritance is structural (file-existence preconditions for Plan 5's `services/` imports to resolve).

**Plan 5 does NOT inherit:**
- The embeddings adapter — Plan 5 introduces `EmbeddingsProvider` as a separate ABC (spec §3.6 line 361 anticipates `providers/vllm_embed.py` if shape diverges). LLMProvider is for chat only.

## What Plan 6 inherits from Plan 4

Plan 6 ("Domain services") starts with:

- `LLMProvider` ABC + factory — domain services that need LLM calls receive a constructed `LLMProvider` via constructor injection. `HandleResolverService` may use LLM for graph-query construction; if so, its constructor takes `LLMProvider`.
- `ContextBudgetService` — Plan 6 services NEVER instantiate this directly. The factory wired in `api/deps.py` (Plan 11) threads it through.
- `LLMConfig` (`base_url` / `api_key` / `model`) — Plan 6 reuses the per-provider Pydantic config shape from Plan 4 Task 3 when defining its sibling `EmbeddingsConfig`. The three fields are the canonical "where to send requests + auth + which model" triple that all Plan 6 embeddings providers extend.
- `LLMProviderFactory` (Plan 4 Task 7) — Plan 6 defines a sibling `EmbeddingsProviderFactory` that mirrors this shape (env-driven provider selection, constructor injection at composition time, single-instance reuse across calls).
- `MockLLMProvider` testing pattern + `--run-live-llm` pytest flag — Plan 6 adds a `MockEmbeddingsProvider` mirror and `--run-live-embeddings` flag extension, both modeled on Plan 4's MockLLMProvider testing pattern (Plan 4 Task 4 — fingerprint-keyed fixture lookup) and the `--run-live-llm` opt-in flag (Plan 4 Task 7 — pytest conftest convention). Plan 6 reuses both conventions for its embeddings adapter.
- `banned-api 'import litellm'` ruff rule (Plan 4 project-level ruff config) — Plan 6 cites this rule when explaining why `providers/litellm.py` is intentionally NOT in per-file-ignores: litellm is structurally banned at the project level; Plan 6's embeddings adapter uses native `httpx` against OpenAI-compatible endpoints, not litellm.

**Plan 6 services that DO NOT call LLM:**
- `TrustMatrixService` — pure-domain trust computation; no LLM.
- `VerifierService` — converts DraftClaim → Claim/Verdict; no LLM (the LLM produced the DraftClaim upstream; the Verifier only verifies).

## What Plan 7 inherits from Plan 4

Plan 7 ("Continuous consumer + traces ingest") inherits from Plan 4:

- `httpx.AsyncClient` shared-transport pattern (precedent from `OpenAIEmbeddingsProvider`) — Plan 7's `TempoClient` copies the shared-transport convention: single `httpx.AsyncClient` instance constructed in `__init__` and reused across calls (connection pooling, retry semantics, structured-output decoding). Plan 4 Task 6 establishes the pattern; Plan 7 mirrors it for Tempo's trace ingest endpoint.
- `OpenAIEmbeddingsProvider` + `OpenAIChatProvider` (httpx-dep precedent) — Plan 4's two real-provider implementations are the proof-of-viability for the httpx runtime dep without litellm. Plan 7's TempoClient cites these as the precedent justifying httpx as the canonical async HTTP client.
- `LLMConfig` sibling-under-`Settings` pattern (Plan 4 Task 3 convention) — Plan 4 places `LLMConfig` as a sibling of `Settings` (the canonical Pydantic-Settings root class), not nested as a field. Plan 7's `TracesConfig` consumes this convention for its Tempo / trace-ingest configuration.

## What Plan 8 inherits from Plan 4

Plan 8 ("Handle resolution + Sourcebot") inherits from Plan 4:

- `httpx` runtime dep — Plan 4 introduced httpx as the canonical async HTTP client (per Plan 7 L28 quote and Plan 8's canonical table at L27 attributing httpx to Plan 4). Plan 8's SourcebotClient consumes httpx for its Sourcebot API calls. FIX_HINT (round-2 coordinator note): Plan 7 vs Plan 4 attribution anomaly surfaced in plan-8-edges.md — coordinator must verify the originator is Plan 4 not Plan 7 (this manifest assumes Plan 4 per Plan 8's canonical-table attribution).
- `--run-live-llm` pytest CLI option — Plan 8 explicitly mirrors this Plan 4 Task 7 convention for its own `--run-live-sourcebot` flag (per Plan 8 L97 quote). Both flags follow the same opt-in semantics: live external service tests are skipped by default; the flag enables them in CI under environment-protected secrets.
- Per-flag branching pattern in `pytest_collection_modifyitems` — Plan 4 Task 7's conftest establishes the readable per-flag branching convention (one explicit `if config.getoption("--run-live-X"): ...` branch per flag, rather than a dict-driven dispatch). Plan 8 L170 quote anchors to this: "Plan 4 already established that the per-flag branching is more readable."
- `[tool.ruff.lint.flake8-tidy-imports.banned-api]` block — Plan 1 Task 4 Step 2 establishes the banned-api block (banning `import openai|anthropic|litellm|ollama` outside `adapters/llm/providers/*.py`); Plan 4 Task 1 verifies it intact and is its first real consumer (Plan 4's providers are the files the exemption unblocks). Plan 8 L172-180 appends two SourcebotClient guards (banning direct Sourcebot-API access outside the adapter) extending the same block.
- `per-file-ignores` convention for `src/codeoracle/adapters/llm/providers/` — Plan 8 L180 quote: "mirroring how Plan 4 unblocks ... inside adapters/llm/providers/." Plan 1 Task 4 Step 3 ships the `providers/*.py` glob per-file-ignore; Plan 4 is the first plan to exercise it (its providers are the files unblocked), so Plan 8 cites Plan 4 as the exemplar when it reuses the same glob convention for its SourcebotClient adapter file.
- `LLMConfig` per-adapter `Settings` pattern (Plan 4 Task 3) — Plan 8 L379 quote anchors here: "Matches the per-adapter Settings pattern from ... Plan 4 (LLMConfig)." Plan 8's `SourcebotSettings` adapter-config copies Plan 4's per-adapter Pydantic-Settings convention (one Settings subclass per external service, scoped to that adapter's env-prefix).

## What Plan 9 inherits from Plan 4

Plan 9 ("Verifier + trust matrix") inherits from Plan 4:

- `tests/unit/services/__init__.py` — Plan 4 creates this test-package `__init__.py` as part of its Task 4 (services/context_budget.py + tests). Plan 9 L118 quote: "already exists (Plan 4) — do not create it." Plan 9's verifier-service tests live under `tests/unit/services/` and inherit the package-existence precondition.

## What Plan 10 inherits from Plan 4

Plan 10 ("Agent orchestration") inherits from Plan 4 (these are umbrella-split items from plan-4's `provides.3` row — enumerated separately here so plan-10's per-identifier imports match):

- `ChatMessage.template_id` field — Plan 4 Task 2 extends `src/codeoracle/models/llm.py`'s `ChatMessage` model with the optional `template_id: str | None = None` field. Plan 10's agent constructs `ChatMessage` with `template_id` set (e.g., `"agent.system.v1"`, `"agent.user.tool_call.v1"`) so the request fingerprint stays stable across cosmetic prompt edits. The only Plan-3-model change Plan 4 makes.
- `request_fingerprint` helper (`codeoracle.adapters.llm.fingerprint.request_fingerprint(messages, tools, *, model, temperature, max_tokens, structured_output) -> str`) — Plan 4 Task 5 ships the deterministic SHA256-based hash over messages + tools + spec §5.7 "key parameters" (model required; temperature/max_tokens/structured_output optional). Plan 10's agent uses this for the template_id-aware request hashing (per plan-10.imports.31 "request-fingerprint (template_id hashing)").
- `LLMProvider.aclose()` lifecycle hook — Plan 4 Task 4 ships the ABC default no-op `async def aclose(self) -> None`. All four production providers override it: Tasks 8 + 9 (`OpenAIProvider` / `AnthropicProvider`, each `await self._client.close()` on the Stainless-generated SDK wrapper) and Tasks 10 + 11 (`VLLMProvider` / `OllamaProvider`, each `await self._client.aclose()` on the raw `httpx.AsyncClient`). The `close()` vs `aclose()` name asymmetry is intrinsic to the underlying libraries (Stainless SDK exposes `close()`; raw `httpx.AsyncClient` exposes `aclose()`), not a bug. Plan 10's agent process (Task 11) registers `LLMProvider.aclose()` in its FastAPI lifespan shutdown so HTTP/SDK connections drain cleanly before process exit. The default-no-op ABC method preserves instantiability for `MockLLMProvider` and any test-stub subclass that doesn't need explicit drain. Plan 11 (API process) and Plan 12 (MCP process) inherit the same shutdown pattern when written.

## What downstream plans inherit from Plan 4

The pre-pivot "Plan 7 = agent + API + MCP + E2E" lump expands post-pivot to four downstream plans (10 / 11 / 12 / 13). The Plan 4 facade and supporting infrastructure are shared across all four:

- **The full LLM facade** (`LLMProvider` ABC + `LLMProviderFactory` + provider implementations) — Plans 10 (agent), 11 (API), 12 (MCP) all receive a constructed `LLMProvider` via DI for the LLM calls each process makes.
- **`ContextBudgetService`** — Plan 11 (FastAPI) wires it in `api/deps.py` and threads it into the factory; Plan 10's agent receives the same already-budget-gated provider via the same factory.
- **`.github/workflows/test-live-llm.yml`** — Plan 13 (E2E vertical slice) EXTENDS this workflow with the 9 slice integration tests (adds them under the same `live_llm` marker; the workflow runs them automatically because it does `pytest -m live_llm tests/integration/`).
- **`tests/fixtures/llm_scripts/`** — Plan 13 populates this with the 4 slice fixture YAMLs (one per LLM-bearing slice gate; gate 3 is verifier-integration with no LLM call, so it needs no fixture — the 9 slice integration tests replay these 4 scripts). The canonical example written in Plan 4 Task 7 Step 2 stays as the format reference.
- **The `request_fingerprint` helper** — Plan 13 (or a parallel dev-tools sub-skill) may add a script that captures live OpenAI responses and writes them as fingerprint-keyed fixtures.
- **`MockLLMProvider` + `MockLLMProviderConfig.scripts_dir`** — Plan 13 runs every integration test against `MockLLMProvider` (per D-10.U3) rather than a live provider; the deterministic stub (Plan 4 Task 7) replays fingerprint-keyed scripts from `scripts_dir` (see the `tests/fixtures/llm_scripts/` + `request_fingerprint` bullets above).
