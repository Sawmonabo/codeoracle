"""Plan 6 frame-extension tests for `core/exceptions.py`.

Three new TerminalWorkerError subclasses are added: ScipExtractionError,
OpengrepRunError, EmbedExtractionError. They share the WorkerError hierarchy
and are routed by Plan 5's WorkerConsumer to the DLQ (NO retry).
"""

import pytest

from codeoracle.core.exceptions import (
    EmbedExtractionError,
    JoernExtractionError,
    OpengrepRunError,
    ScipExtractionError,
    TerminalWorkerError,
    WorkerError,
)


@pytest.mark.parametrize(
    "cls", [ScipExtractionError, OpengrepRunError, EmbedExtractionError]
)
def test_each_is_terminal_worker_error(cls: type[Exception]) -> None:
    err = cls("boom")
    assert isinstance(err, TerminalWorkerError)
    assert isinstance(err, WorkerError)
    assert str(err) == "boom"


def test_distinct_from_joern_and_from_each_other() -> None:
    # We need each extractor's failures to be catchable independently — code
    # that handles a SCIP failure must NOT accidentally catch an embed failure
    # via the same `except` clause. The minimal contract: each subclass is
    # not a parent of the others.
    siblings = [
        JoernExtractionError,
        ScipExtractionError,
        OpengrepRunError,
        EmbedExtractionError,
    ]
    for cls in siblings:
        instance = cls("x")
        for other in siblings:
            if other is cls:
                continue
            assert not isinstance(instance, other), (
                f"{cls.__name__} should not be a {other.__name__}"
            )
