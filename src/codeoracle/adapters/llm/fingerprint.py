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

Algorithm: Compose a canonical JSON object: { "messages": [
        {"role": ..., "template_id": ..., "content_hash":
        null_if_template_id_set}
        ... ], "tools": [{"name": ..., "parameters_schema": ...}, ...],
      "model": "<model-id>", "temperature": <float-or-null>,
      "max_tokens": <int-or-null>,
      "structured_output": "<module.QualName>-or-null" }
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
        "structured_output": _canonicalize_structured_output(
            structured_output
        ),
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
