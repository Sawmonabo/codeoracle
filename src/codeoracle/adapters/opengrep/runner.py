"""OpenGrep runner boundary."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from codeoracle.core.exceptions import OpengrepRunError

_COMMAND_TIMEOUT_SECONDS = 100.0
_BODY_EXCERPT_CHARS = 500
OpengrepReferenceKind = Literal[
    "import", "call", "string-literal", "comment-mention"
]

_RULE_KIND: dict[str, OpengrepReferenceKind] = {
    "codeoracle-python-imports": "import",
    "codeoracle-python-calls": "call",
    "codeoracle-string-literal": "string-literal",
    "codeoracle-comment-mention": "comment-mention",
}


@dataclass(frozen=True)
class OpengrepFinding:
    """Normalized OpenGrep finding."""

    file_path: str
    symbol_name: str
    line_number: int
    kind: OpengrepReferenceKind
    rule_id: str
    message: str


class OpengrepRunner:
    """Subprocess boundary for OpenGrep."""

    def __init__(
        self,
        *,
        rules_dir: Path | None = None,
        timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self._rules_dir = rules_dir
        self._timeout_seconds = timeout_seconds

    async def verify_binary(self) -> str:
        result = await self._run("opengrep", "--version")
        return result.strip()

    async def scan_file(self, path: Path) -> list[OpengrepFinding]:
        if not path.exists():
            raise OpengrepRunError(f"file not found: {path}")
        config_args: list[str] = []
        if self._rules_dir is not None:
            config_args = ["--config", str(self._rules_dir)]
        payload = await self._run(
            "opengrep",
            "scan",
            *config_args,
            "--json",
            str(path),
        )
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError as exc:
            raise OpengrepRunError("opengrep returned malformed JSON") from exc
        return _findings_from_payload(data)

    async def _run(self, *argv: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise OpengrepRunError(
                "opengrep binary not found on PATH"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise OpengrepRunError(
                f"opengrep timed out after {self._timeout_seconds}s"
            ) from exc

        if proc.returncode != 0:
            error = stderr.decode(errors="replace")[:_BODY_EXCERPT_CHARS]
            output = stdout.decode(errors="replace")[:_BODY_EXCERPT_CHARS]
            raise OpengrepRunError(
                f"opengrep exited {proc.returncode}: stderr={error!r} "
                f"stdout={output!r}"
            )
        return stdout.decode(errors="replace")


def _findings_from_payload(data: Any) -> list[OpengrepFinding]:
    if not isinstance(data, dict):
        raise OpengrepRunError("opengrep JSON root must be an object")
    results = data.get("results", [])
    if not isinstance(results, list):
        raise OpengrepRunError("opengrep JSON results must be a list")
    return [_finding_from_result(item) for item in results]


def _finding_from_result(item: Any) -> OpengrepFinding:
    if not isinstance(item, dict):
        raise OpengrepRunError("opengrep result must be an object")
    rule_id = str(item.get("check_id", ""))
    kind = _RULE_KIND.get(rule_id, "call")
    extra = item.get("extra", {})
    if not isinstance(extra, dict):
        extra = {}
    start = item.get("start", {})
    if not isinstance(start, dict):
        start = {}
    return OpengrepFinding(
        file_path=str(item.get("path", "")),
        symbol_name=_symbol_name(rule_id=rule_id, extra=extra),
        line_number=int(start.get("line", 1)),
        kind=kind,
        rule_id=rule_id,
        message=str(extra.get("message", "")),
    )


def _symbol_name(*, rule_id: str, extra: dict[str, Any]) -> str:
    metavars = extra.get("metavars", {})
    if not isinstance(metavars, dict):
        metavars = {}
    match rule_id:
        case "codeoracle-python-imports":
            return _metavar_text(metavars, "$MODULE")
        case "codeoracle-python-calls":
            return _metavar_text(metavars, "$FUNC")
        case "codeoracle-string-literal":
            return _metavar_text(metavars, "$VALUE")
        case "codeoracle-comment-mention":
            return str(extra.get("lines", "")).strip()
        case _:
            return str(extra.get("lines", "")).strip() or rule_id


def _metavar_text(metavars: dict[str, Any], name: str) -> str:
    value = metavars.get(name, {})
    if not isinstance(value, dict):
        return ""
    text = value.get("abstract_content") or value.get("text") or ""
    return str(text).strip().strip('"').strip("'")
