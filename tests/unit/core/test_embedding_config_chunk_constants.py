"""EmbeddingConfig now carries embed_chunk_lines + embed_chunk_overlap.

Defaults: 100 lines per chunk, 20-line overlap between adjacent chunks. Both
fields are env-overridable via the parent Settings(BaseSettings)'s nested-env
cascade — env vars `CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES` and
`CODEORACLE_EMBEDDING__EMBED_CHUNK_OVERLAP` (see Plan 3 `core/config.py` for
the env_prefix='CODEORACLE_' + env_nested_delimiter='__' parent config).

EmbeddingConfig itself is a `BaseModel` (not `BaseSettings`) per Plan 3 — env
binding lives on the parent. Tests that construct EmbeddingConfig directly
must therefore pass the Plan 3 required fields (`model`, `dim`); tests that
exercise env-override go through `Settings()` so the parent's cascade fires.
"""

import pathlib

import pytest

from codeoracle.core.config import EmbeddingConfig, Settings


# Helper: Plan 3 made `model` and `dim` required-no-default to fail fast on
# missing env injection. Tests that don't care about those fields fill them
# with sane defaults here.
#
# Why we pass `kind="mock"` even though the validator doesn't require it:
#   - Plan 6 Task 7 adds `kind` as a Literal["openai", "vllm", "mock"] field
#     with default "openai". The api_key precondition for openai/vllm lives
#     on OpenAIEmbeddingsProvider.__init__, NOT on the validator — so a test
#     can construct EmbeddingConfig() with kind="openai" + api_key="" today
#     and the construction succeeds. Defensive `kind="mock"` here protects
#     us from a hypothetical future kind-keyed validator rule (e.g., "kind
#     must match base_url scheme") that might be added without a sweep of
#     test-helpers.
#   - The `kind` field doesn't exist yet in Task 3 (it's added by Task 7),
#     but Pydantic v2 BaseModel defaults to `extra='ignore'`, so unknown
#     kwargs are silently dropped pre-Task 7 and honored post-Task 7 —
#     forward-compatible regardless of which task this helper runs against.
def _ec(**overrides: object) -> EmbeddingConfig:
    base: dict[str, object] = {
        "model": "text-embedding-3-small",
        "dim": 1536,
        "kind": "mock",
    }
    base.update(overrides)
    return EmbeddingConfig.model_validate(base)


def test_default_chunk_lines_is_100() -> None:
    config = _ec()
    assert config.embed_chunk_lines == 100


def test_default_chunk_overlap_is_20() -> None:
    config = _ec()
    assert config.embed_chunk_overlap == 20


def test_env_override_chunk_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """
    Env override flows through parent Settings's `CODEORACLE_EMBEDDING__`
    cascade.

    NOTE: we drive env override through Settings(), not EmbeddingConfig
    directly — EmbeddingConfig is a BaseModel and does not itself read env.
    """
    monkeypatch.chdir(tmp_path)  # escape repo .env so it doesn't bleed in
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES", "50")
    settings = Settings.model_validate({})
    assert settings.embedding.embed_chunk_lines == 50


def test_env_override_chunk_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__EMBED_CHUNK_OVERLAP", "5")
    settings = Settings.model_validate({})
    assert settings.embedding.embed_chunk_overlap == 5


def test_default_doc_chunk_max_lines_is_200() -> None:
    """ParagraphChunker (doc files) is bounded by doc_chunk_max_lines.

    Default 200 — well above typical markdown paragraph length (5-20 lines),
    so most paragraphs sail through unsplit; only pathologically long blocks
    (large code-fence dumps, generated tables) hit the split path.
    """
    config = _ec()
    assert config.doc_chunk_max_lines == 200


def test_env_override_doc_chunk_max_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DOC_CHUNK_MAX_LINES", "80")
    settings = Settings.model_validate({})
    assert settings.embedding.doc_chunk_max_lines == 80


def test_overlap_must_be_less_than_chunk_size() -> None:
    """Sanity guard: overlap >= chunk_size would create infinite loops in
    the chunker. The model validator rejects this."""
    with pytest.raises(ValueError, match="overlap"):
        _ec(embed_chunk_lines=50, embed_chunk_overlap=50)
    with pytest.raises(ValueError, match="overlap"):
        _ec(embed_chunk_lines=50, embed_chunk_overlap=100)


def test_degenerate_stride_rejected() -> None:
    """D-6.13: overlap < chunk_lines is not sufficient. A tiny stride
    (chunk_lines - overlap < _MIN_CHUNK_STRIDE) yields O(file) near-duplicate
    chunks even though overlap < chunk_lines holds. The validator rejects it.
    """
    # stride 1 — the canonical degenerate case
    with pytest.raises(ValueError, match="chunk"):
        _ec(embed_chunk_lines=2, embed_chunk_overlap=1)
    # stride 5 — still below the floor of 10
    with pytest.raises(ValueError, match="chunk"):
        _ec(embed_chunk_lines=100, embed_chunk_overlap=95)
    # stride exactly at the floor (10) is accepted
    cfg = _ec(embed_chunk_lines=30, embed_chunk_overlap=20)
    assert cfg.embed_chunk_lines - cfg.embed_chunk_overlap == 10


def test_negative_chunk_size_rejected() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        _ec(embed_chunk_lines=0)
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        _ec(embed_chunk_lines=-10)


def test_negative_doc_chunk_max_lines_rejected() -> None:
    """doc_chunk_max_lines must be >= 1; zero/negative would mean
    ParagraphChunker emits zero chunks for every doc file (silently broken)."""
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        _ec(doc_chunk_max_lines=0)
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        _ec(doc_chunk_max_lines=-1)
