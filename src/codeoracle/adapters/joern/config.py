"""JoernConfig — paths + timeouts. Env-loaded via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class JoernConfig(BaseSettings):
    """Where Joern lives and how long to wait.

    `joern_parse_bin` and `joern_export_bin` default to the CLI names —
    on the PATH if joern-cli is installed system-wide. In Docker, both
    resolve under /opt/joern/joern-cli/.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_JOERN__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    joern_parse_bin: str = Field(
        default="joern-parse",
        min_length=1,
        description=(
            "Path or name of the joern-parse binary. Default: 'joern-parse'."
        ),
    )
    joern_export_bin: str = Field(
        default="joern-export",
        min_length=1,
        description="Path or name of joern-export binary.",
    )
    parse_timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=600,
        description="Max seconds for one joern-parse subprocess.",
    )
    export_timeout_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Max seconds for one joern-export subprocess.",
    )
    work_dir: str = Field(
        default="/tmp/joern",
        min_length=1,
        description=(
            "Filesystem dir under which JoernClient creates per-extraction "
            "scratch subdirs. /tmp/joern is a reasonable default in Docker."
        ),
    )
