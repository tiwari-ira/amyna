"""Tool-call dispatcher for the voice model.

The model supplies only tool-specific arguments. ``case_id`` (and ``call_id``
where relevant) are injected from the call session, so a prompt-injected
"look up case X" can never reach another patient's data (decision D13).
"""

import json
from typing import Any, Protocol

from claimshield_contracts.tool_api import CoreToolError, ToolName

from .prompts import VOICE_TOOL_SCHEMAS

CONTROL_KEY = "_control"
CONTROL_PAUSE = "pause"

_ALLOWED_ARGS: dict[str, frozenset[str]] = {
    schema["name"]: frozenset(schema["parameters"]["properties"]) for schema in VOICE_TOOL_SCHEMAS
}
_CALL_SCOPED = frozenset({ToolName.RECORD_INSURER_RESPONSE.value, ToolName.REQUEST_HUMAN.value})


class VoiceToolBackend(Protocol):
    """Executes a Core tool for the voice identity and returns JSON-safe output."""

    def call(self, tool: ToolName, payload: dict[str, Any]) -> dict[str, Any]: ...


class ToolCallDispatcher:
    def __init__(self, backend: VoiceToolBackend, *, case_id: str, call_id: str) -> None:
        self._backend = backend
        self._case_id = case_id
        self._call_id = call_id

    def dispatch(self, name: str, arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        """Validate and run one tool call. Errors are returned to the model, not raised."""
        if name not in _ALLOWED_ARGS:
            return {"error": "unknown_tool", "message": f"Tool '{name}' is not available."}
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except json.JSONDecodeError:
            return {"error": "invalid_arguments", "message": "Arguments must be a JSON object."}
        unexpected = sorted(set(args) - _ALLOWED_ARGS[name])
        if unexpected:
            return {
                "error": "invalid_arguments",
                "message": f"Unexpected argument(s): {', '.join(unexpected)}",
            }
        payload = {**args, "case_id": self._case_id}
        if name in _CALL_SCOPED:
            payload["call_id"] = self._call_id
        try:
            result = self._backend.call(ToolName(name), payload)
        except CoreToolError as exc:
            return {"error": exc.code, "message": exc.message}
        if name == ToolName.REQUEST_HUMAN.value:
            result[CONTROL_KEY] = CONTROL_PAUSE
        return result
