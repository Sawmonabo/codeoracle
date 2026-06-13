"""Platform-wide exception hierarchy.

Every domain-specific exception inherits from CodeOracleError. Process
entrypoints (api, mcp, agent, workers) catch CodeOracleError at the top
level to log + return a structured error response; uncaught builtins
or third-party exceptions bubble up as 500-class infrastructure faults.
"""


class CodeOracleError(Exception):
    """Base exception for all platform-defined errors."""


class ConfigError(CodeOracleError):
    """
    Raised when configuration is missing, malformed, or self-inconsistent.
    """


class DatabaseError(CodeOracleError):
    """
    Raised by db/ for any persistence-layer failure not modeled more precisely.
    """


class MigrationError(DatabaseError):
    """Raised when alembic migrations fail to apply or rollback safely."""


class LLMProviderError(CodeOracleError):
    """Raised by LLM provider adapters."""


class WorkerError(CodeOracleError):
    """Base class for worker-framework errors."""


class TransientWorkerError(WorkerError):
    """Raised when a worker message should be retried."""


class TerminalWorkerError(WorkerError):
    """Raised when a worker message should be dead-lettered."""


class JoernExtractionError(WorkerError):
    """Raised when Joern extraction fails."""


class ScipExtractionError(WorkerError):
    """Raised when SCIP extraction fails."""


class OpengrepRunError(WorkerError):
    """Raised when OpenGrep extraction fails."""


class EmbedExtractionError(WorkerError):
    """Raised when embedding extraction fails."""


class GraphQueryError(CodeOracleError):
    """Raised when graph repository queries fail."""


class SnapshotConflictError(CodeOracleError):
    """Raised when a snapshot already exists for a conflicting state."""


class AdvisoryLockTimeoutError(CodeOracleError):
    """Raised when the single-flight reindex lock cannot be acquired."""


class SingleInstanceContendedError(AdvisoryLockTimeoutError):
    """Raised when another process already owns the single-instance lock."""


SingleInstanceContended = SingleInstanceContendedError


class ResolverError(CodeOracleError):
    """Base class for handle-resolution failures."""


class UnsupportedHandleTypeError(ResolverError):
    """Raised when no resolver supports a handle type."""


class SourcebotError(CodeOracleError):
    """Base class for Sourcebot adapter failures."""


class SourcebotTransientError(SourcebotError):
    """Raised for retryable Sourcebot failures."""


class SourcebotTerminalError(SourcebotError):
    """Raised for non-retryable Sourcebot failures."""


class AgentError(CodeOracleError):
    """Raised by the agent orchestration layer."""


class TransientFetchError(CodeOracleError):
    """Raised when an external fetch can be retried."""


class TerminalFetchError(CodeOracleError):
    """Raised when an external fetch cannot be retried."""
