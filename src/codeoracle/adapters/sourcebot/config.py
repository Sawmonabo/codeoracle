"""SourcebotConfig — env-loaded settings for the Sourcebot HTTP adapter.

Env prefix `CODEORACLE_SOURCEBOT__` (double underscore is the nested-delimiter
convention Plan 3 established; flat fields like base_url use the prefix
+ single name). All values are required at app start — no fallbacks.

The composition root (Plan 11 api/deps.py + Plan 5 workers/_base/wiring.py)
instantiates one SourcebotConfig per process and passes it into
SourcebotClient.
"""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourcebotConfig(BaseSettings):
    """Sourcebot HTTP API configuration.

    Attributes: base_url: scheme://host[:port] of the Sourcebot instance.
                MUST include a scheme; the validator rejects bare host:port.
      api_key: Bearer token used in the Authorization header.
               SecretStr so the value never appears in log/repr.
      timeout_seconds: per-request HTTP timeout. Sourcebot occasionally
                       takes seconds to respond for cold repos; the default
                       (5s) is conservative — raise via env if a deployment
                       indexes very large repos.
      default_repo: the repo identifier passed as `?repo=` to /api/source.
                    Slice scope is single-repo (src/codeoracle/); multi-repo
                    is Plan 11+ composition-root work. Format follows
                    Sourcebot's own repo-naming convention, typically
                    "github.com/<org>/<repo>" — verifiable in the
                    Sourcebot UI's repo list.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_SOURCEBOT__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = Field(
        min_length=1,
        description="scheme://host[:port] for the Sourcebot HTTP API.",
    )
    api_key: SecretStr = Field(
        description="Bearer token for the Authorization header.",
    )
    timeout_seconds: float = Field(
        gt=0.0,
        le=120.0,
        description="Per-request HTTP timeout in seconds.",
    )
    default_repo: str = Field(
        min_length=1,
        description=(
            "Sourcebot's repo identifier, e.g. "
            "'github.com/Sawmonabo/codeoracle'. "
            "Passed as the `repo` query param to /api/source."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def _scheme_required(cls, v: str) -> str:
        if "://" not in v:
            raise ValueError(
                "base_url must include scheme (http:// or https://)"
            )
        # Strip trailing slash so URL composition doesn't double-slash.
        return v.rstrip("/")
