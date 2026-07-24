"""Shared machine-readable output and agent execution policy helpers."""

from contextvars import ContextVar
from dataclasses import dataclass, field, is_dataclass
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any, Dict, IO, Iterable, List, Mapping, Optional

import click
import typer

AGENT_SCHEMA_VERSION = "pltr-agent-v1"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "secret",
    "token",
)


@dataclass(frozen=True)
class AgentSettings:
    """Execution settings for one CLI invocation."""

    enabled: bool = False
    non_interactive: bool = False


_settings: ContextVar[AgentSettings] = ContextVar(
    "pltr_agent_settings", default=AgentSettings()
)


class AgentPolicyError(click.ClickException, RuntimeError):
    """Raised when an agent invocation violates an execution policy.

    A ClickException so every command -- including the ones that prompt outside
    a try/except -- fails with a formatted policy message and exit code 2
    instead of an unhandled traceback.
    """

    exit_code = 2


@dataclass
class _AgentBuffer:
    """Accumulated agent output for one CLI invocation."""

    payloads: List[Dict[str, Any]] = field(default_factory=list)
    messages: List[Dict[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.payloads and not self.messages


# ponytail: one module-level buffer, not a ContextVar, because a CLI process
# serves exactly one invocation. Reset on both configure and flush so repeated
# in-process invocations (CliRunner in the test suite) never leak into each
# other. Move to a ContextVar only if pltr ever runs commands concurrently.
_buffer = _AgentBuffer()


def reset_agent_output() -> None:
    """Drop any buffered agent output without emitting it."""
    _buffer.payloads.clear()
    _buffer.messages.clear()


def configure_agent_settings(
    *, enabled: bool = False, non_interactive: bool = False
) -> None:
    """Set agent execution settings for the current CLI invocation."""
    _settings.set(AgentSettings(enabled=enabled, non_interactive=non_interactive))
    reset_agent_output()


def agent_mode_enabled() -> bool:
    """Return whether the current invocation requests agent output."""
    return _settings.get().enabled


def non_interactive_enabled() -> bool:
    """Return whether prompts are forbidden for the current invocation."""
    return _settings.get().non_interactive or agent_mode_enabled()


def resolve_output_format(format_type: str) -> str:
    """Use agent output when the invocation globally requested it."""
    if agent_mode_enabled():
        return "agent"
    return format_type


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    # Cursor continuation tokens are control-plane pagination values, not
    # credentials. They must remain visible in the agent contract so callers
    # can resume a bounded operation.
    if normalized.endswith("_page_token") or normalized == "page_token":
        return False
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_value(value: Any, key: Optional[str] = None) -> Any:
    """Convert a value to JSON data while redacting credential-like fields."""
    if key and _is_sensitive_key(key):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return redact_value(vars(value))
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return [redact_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return redact_value(
            {
                item_key: item_value
                for item_key, item_value in vars(value).items()
                if not item_key.startswith("_")
            }
        )
    return str(value)


def agent_envelope(
    data: Any = None,
    *,
    meta: Optional[Mapping[str, Any]] = None,
    warnings: Optional[Iterable[str]] = None,
    errors: Optional[Iterable[Mapping[str, Any]]] = None,
    pagination: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the stable agent result envelope."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "data": redact_value(data),
        "meta": redact_value(dict(meta or {})),
        "warnings": redact_value(list(warnings or [])),
        "errors": redact_value(list(errors or [])),
        "pagination": redact_value(dict(pagination)) if pagination else None,
        "artifacts": redact_value(list(artifacts or [])),
    }


def render_agent_json(
    data: Any = None,
    *,
    meta: Optional[Mapping[str, Any]] = None,
    warnings: Optional[Iterable[str]] = None,
    errors: Optional[Iterable[Mapping[str, Any]]] = None,
    pagination: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Iterable[Mapping[str, Any]]] = None,
) -> str:
    """Serialize an agent envelope as newline-terminated JSON."""
    return (
        json.dumps(
            agent_envelope(
                data,
                meta=meta,
                warnings=warnings,
                errors=errors,
                pagination=pagination,
                artifacts=artifacts,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def require_confirmation(
    message: str,
    *,
    confirmed: bool = False,
    option_name: str = "--force",
) -> bool:
    """Require explicit confirmation without prompting agent invocations."""
    if confirmed:
        return True
    if non_interactive_enabled():
        detail = f"{message} Non-interactive mode requires explicit {option_name}."
        if agent_mode_enabled():
            # Buffer before raising so the flush still emits one envelope
            # carrying the refusal, rather than leaving an agent empty stdout.
            buffer_agent_message(detail, level="error")
        raise AgentPolicyError(detail)
    return typer.confirm(message)


def render_agent_message(
    message: str,
    *,
    level: str = "info",
) -> str:
    """Serialize a status message without Rich markup or ANSI codes."""
    warnings = [message] if level == "warning" else []
    errors = [{"type": level, "message": message}] if level == "error" else []
    return render_agent_json(
        meta={"message": message}, warnings=warnings, errors=errors
    )


def buffer_agent_payload(
    data: Any = None,
    *,
    meta: Optional[Mapping[str, Any]] = None,
    warnings: Optional[Iterable[str]] = None,
    errors: Optional[Iterable[Mapping[str, Any]]] = None,
    pagination: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Iterable[Mapping[str, Any]]] = None,
) -> None:
    """Record one result for the single envelope emitted at invocation end.

    Callers must never write an envelope to stdout themselves. A command that
    renders more than once would otherwise emit several top-level JSON
    documents, and ``json.loads(stdout)`` would fail for every one of them.
    """
    _buffer.payloads.append(
        {
            "data": data,
            "meta": dict(meta or {}),
            "warnings": list(warnings or []),
            "errors": list(errors or []),
            "pagination": dict(pagination) if pagination else None,
            "artifacts": list(artifacts or []),
        }
    )


def buffer_agent_message(message: str, *, level: str = "info") -> None:
    """Record a status message for the single envelope."""
    _buffer.messages.append({"level": level, "message": message})


def agent_output_pending() -> bool:
    """Return whether anything is waiting to be flushed."""
    return not _buffer.is_empty()


def build_agent_output() -> Optional[Dict[str, Any]]:
    """Merge every buffered result into one envelope, or None if nothing was recorded."""
    if _buffer.is_empty():
        return None

    payloads = _buffer.payloads
    if not payloads:
        data: Any = None
    elif len(payloads) == 1:
        data = payloads[0]["data"]
    else:
        data = [payload["data"] for payload in payloads]

    meta: Dict[str, Any] = {}
    warnings: List[Any] = []
    errors: List[Any] = []
    artifacts: List[Any] = []
    pagination: Optional[Mapping[str, Any]] = None
    if len(payloads) > 1:
        # data is a list here, and a flat meta merge would let a later payload
        # overwrite an earlier one's operation. Keep the per-result metadata
        # positionally aligned with data[i].
        meta["results"] = [dict(payload["meta"]) for payload in payloads]
    for payload in payloads:
        meta.update(payload["meta"])
        warnings.extend(payload["warnings"])
        errors.extend(payload["errors"])
        artifacts.extend(payload["artifacts"])
        if payload["pagination"] is not None:
            pagination = payload["pagination"]

    if _buffer.messages:
        meta["messages"] = list(_buffer.messages)
        # Preserve the routing render_agent_message used, so a warning or error
        # stays discoverable in its own envelope field rather than only in meta.
        for entry in _buffer.messages:
            if entry["level"] == "warning":
                warnings.append(entry["message"])
            elif entry["level"] == "error":
                errors.append({"type": "error", "message": entry["message"]})

    return agent_envelope(
        data,
        meta=meta,
        warnings=warnings,
        errors=errors,
        pagination=pagination,
        artifacts=artifacts,
    )


def flush_agent_output(stream: Optional[IO[str]] = None) -> Optional[str]:
    """Write the one envelope for this invocation and clear the buffer."""
    envelope = build_agent_output()
    reset_agent_output()
    if envelope is None:
        return None
    rendered = json.dumps(envelope, indent=2, sort_keys=True) + "\n"
    (stream or sys.stdout).write(rendered)
    return rendered
