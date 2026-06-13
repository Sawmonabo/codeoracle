"""Plan 5 Task 3: WorkerMessage + RetryPolicy unit tests."""

import pytest
from pydantic import ValidationError

from codeoracle.workers._base.message import RetryPolicy, WorkerMessage


class _ConcreteMessage(WorkerMessage):
    """Test-local concrete subclass."""

    file_path: str


def _assign_for_validation(
    instance: object,
    field_name: str,
    value: object,
) -> None:
    setattr(instance, field_name, value)


def test_worker_message_forbids_extra_fields():
    with pytest.raises(ValidationError):
        _ConcreteMessage.model_validate(
            {"batch_id": "abc", "file_path": "x.py", "unknown": "boom"}
        )


def test_worker_message_is_frozen():
    m = _ConcreteMessage(batch_id="abc", file_path="x.py")
    with pytest.raises(ValidationError):
        _assign_for_validation(m, "batch_id", "xyz")


def test_retry_policy_should_retry_until_max_attempts():
    p = RetryPolicy(max_attempts=3)
    assert p.should_retry(1) is True
    assert p.should_retry(2) is True
    assert p.should_retry(3) is False
    assert p.should_retry(4) is False
