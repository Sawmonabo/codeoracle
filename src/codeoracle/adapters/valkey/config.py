"""ValkeyConfig — env-loaded settings for the queue client.

A standalone pydantic-settings BaseSettings (same approach as LLMConfig) that
reads a root-style ``CODEORACLE_VALKEY__*`` prefix — double underscore,
distinct from
LLMConfig's flat ``CODEORACLE_LLM_`` (single). Both prefix forms are sanctioned
by the
.env convention; the difference is deliberate, so do not "unify" them — the
config reads ``CODEORACLE_VALKEY__*`` and every env source must set that exact
prefix.
"""

from typing import Any, cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ValkeyConfig(BaseSettings):
    """Connection + tuning parameters for the Valkey queue client.

    All fields are required (no defaults) so misconfiguration fails fast
    at process start, never silently at first publish.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_VALKEY__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: str = Field(
        min_length=1,
        description=(
            "Valkey connection URL — e.g., 'valkey://valkey:6379/0' or "
            "'redis://valkey:6379/0' (the protocols are interchangeable)."
        ),
    )
    pool_min: int = Field(ge=1, le=64, description="Min pool size.")
    pool_max: int = Field(ge=1, le=256, description="Max pool size.")
    block_ms_default: int = Field(
        ge=0,
        le=60_000,
        description=(
            "Default XREADGROUP BLOCK milliseconds — 0 = non-blocking. "
            "Workers typically use 5000 (5s) to avoid busy-spinning."
        ),
    )
    max_in_flight_per_consumer: int = Field(
        ge=1,
        le=1000,
        description="XREADGROUP COUNT cap per fetch — backpressure knob.",
    )
    stream_maxlen: int = Field(
        ge=1,
        description=(
            "Per-stream MAXLEN applied to every XADD (with approximate "
            "trimming). Without it, streams retain every entry forever — "
            "XACK clears only the PEL, never the stream — so cross-reindex "
            "accumulation grows unbounded until the Valkey process is "
            "OOM-killed. MUST exceed the peak unprocessed backlog of a "
            "single reindex, which equals the target's file count: "
            "IndexingService._enqueue_all fans out one message per file per "
            "extractor-kind with no in-flight throttle, and the completion "
            "barrier (_wait_for_completion) polls until every file reaches a "
            "terminal outcome. A value below that floor makes approximate "
            "trimming evict never-delivered entries, stranding their "
            "snapshot_file_outcomes rows so the barrier counts forever and "
            "the reindex never completes. No upper bound — it scales with "
            "the largest target's file count; size it there with headroom "
            "(e.g. 1_000_000)."
        ),
    )


def load_valkey_config() -> ValkeyConfig:
    """Load ValkeyConfig from the environment.

    BaseSettings supports zero-argument env construction at runtime, but ty
    sees the required fields as constructor parameters. Centralizing the cast
    keeps call sites typed without weakening the runtime fail-fast contract.
    """
    config_cls = cast(Any, ValkeyConfig)
    return config_cls()
