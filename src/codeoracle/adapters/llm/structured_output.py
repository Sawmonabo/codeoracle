"""Provider-neutral helpers for schema-bound LLM responses."""

from collections.abc import MutableMapping, MutableSequence
from typing import cast

from pydantic import BaseModel

from codeoracle.core.exceptions import LLMProviderError


def pydantic_json_schema(target: type, *, provider: str) -> dict[str, object]:
    """Return a JSON schema for a Pydantic structured-output target."""
    if not (isinstance(target, type) and issubclass(target, BaseModel)):
        raise LLMProviderError(
            f"{provider} structured_output requires a pydantic.BaseModel "
            f"subclass; got {target!r}."
        )
    schema = target.model_json_schema()
    _disallow_extra_properties(schema)
    return schema


def _disallow_extra_properties(node: object) -> None:
    if isinstance(node, MutableMapping):
        mapping = cast("MutableMapping[str, object]", node)
        if (
            mapping.get("type") == "object"
            and "additionalProperties" not in mapping
        ):
            mapping["additionalProperties"] = False
        for value in mapping.values():
            _disallow_extra_properties(value)
    elif isinstance(node, MutableSequence):
        sequence = cast("MutableSequence[object]", node)
        for item in sequence:
            _disallow_extra_properties(item)
