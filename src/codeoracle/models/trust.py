"""Trust-related enums. Consumed by models/evidence.py and (in Plan 9)
by services/trust_matrix.py."""

from enum import Enum, StrEnum


class SourceKind(StrEnum):
    """Where a citation comes from. Distinct from HandleType — same physical
    file can be evidence of different *kinds* (source code, test, doc, schema).
    """

    CODE = "code"
    TEST = "test"
    DOC = "doc"
    SCHEMA = "schema"
    TRACE = "trace"


class TrustLevel(int, Enum):
    """Coarse-grained trust bands used in policy decisions.

    Plan 9's TrustMatrixService produces fine-grained 0-10 scores; this enum
    lets services compare those scores against named thresholds without magic
    numbers leaking into business logic.
    """

    UNTRUSTED = 0
    LOW = 3
    MEDIUM = 6
    HIGH = 9
    AUTHORITATIVE = 10
