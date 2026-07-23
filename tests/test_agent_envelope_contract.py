"""Contract guard: under --agent, stdout is exactly one JSON envelope.

Phase 1 added `test_sdk_attribute_contract.py` so a command can never again
call an SDK path that does not exist. This is the output-side twin: it walks
every command registered on the Typer app, invokes it under `--agent` with the
service layer stubbed, and asserts stdout parses with a single `json.loads`.

The audit that motivated this measured 0 of 98 mutating commands satisfying
that contract, because `print_success` and the formatters each emitted their
own complete envelope. Nothing here mocks the output layer -- stdout is read
back exactly as a caller would receive it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Tuple
from unittest.mock import MagicMock, patch

import click
import pytest
from typer.testing import CliRunner
from typer.main import get_command

from pltr.cli import app
from pltr.utils.agent_output import (
    AGENT_SCHEMA_VERSION,
    agent_envelope,
    buffer_agent_message,
    buffer_agent_payload,
    configure_agent_settings,
    flush_agent_output,
)

runner = CliRunner()

ENVELOPE_KEYS = {
    "schema_version",
    "data",
    "meta",
    "warnings",
    "errors",
    "pagination",
    "artifacts",
}

# Commands whose callback never reaches the output layer under a stubbed
# service: they write shell rc files or start an interactive REPL. They are
# reported by name rather than silently skipped, so the coverage assertion
# below stays honest.
NOT_INVOCABLE = {
    "completion",
    "shell",
}


def _walk(
    command: click.Command, path: Tuple[str, ...] = ()
) -> Iterator[Tuple[str, ...]]:
    """Yield the argv path of every leaf command on the app."""
    if isinstance(command, click.Group):
        for name, sub in command.commands.items():
            yield from _walk(sub, path + (name,))
    else:
        if path:
            yield path


def all_command_paths() -> List[Tuple[str, ...]]:
    return sorted(_walk(get_command(app)))


def test_app_exposes_commands():
    """A silent zero-command walk would make every assertion below vacuous."""
    assert len(all_command_paths()) > 100


@pytest.mark.parametrize("path", all_command_paths(), ids=lambda p: " ".join(p))
def test_agent_stdout_is_one_envelope(path: Tuple[str, ...]):
    """Invoking any command under --agent yields one parseable document."""
    if path[0] in NOT_INVOCABLE:
        pytest.skip(f"{path[0]} does not reach the output layer")

    # No credentials, no network: every auth path raises, which drives each
    # command down its error branch. That branch is exactly where the
    # concatenated-envelope bug lived, so it is the right thing to measure.
    with patch("pltr.auth.storage.CredentialStorage", MagicMock()):
        result = runner.invoke(app, ["--agent", *path], catch_exceptions=True)

    stdout = result.stdout
    if not stdout.strip():
        # Nothing written is contract-compliant: the caller reads the exit code
        # and stderr. Emitting *several* documents is the failure this guards.
        return

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as error:  # pragma: no cover - failure path
        pytest.fail(
            f"`pltr --agent {' '.join(path)}` wrote stdout that is not one JSON "
            f"document ({error}). First 400 chars:\n{stdout[:400]}"
        )

    assert isinstance(parsed, dict), f"envelope must be an object, got {type(parsed)}"
    assert parsed.get("schema_version") == AGENT_SCHEMA_VERSION
    assert ENVELOPE_KEYS <= set(parsed), f"missing keys: {ENVELOPE_KEYS - set(parsed)}"


def test_coverage_is_reported():
    """Fail loudly if the skip list ever grows to hide most of the surface."""
    paths = all_command_paths()
    skipped = [p for p in paths if p[0] in NOT_INVOCABLE]
    assert len(skipped) / len(paths) < 0.05, (
        f"{len(skipped)}/{len(paths)} commands are excluded from the agent "
        "envelope contract; the guard no longer measures the real surface"
    )


class _Sink:
    """Collects writes so a flush under test never touches real stdout."""

    def __init__(self) -> None:
        self.text = ""

    def write(self, value: str) -> int:
        self.text += value
        return len(value)


class TestEnvelopeMerging:
    """The buffer must collapse many writes into one envelope."""

    def setup_method(self):
        configure_agent_settings(enabled=True, non_interactive=False)

    def teardown_method(self):
        configure_agent_settings(enabled=False, non_interactive=False)

    def _flush(self) -> Dict[str, Any]:
        rendered = flush_agent_output(stream=_Sink())
        assert rendered is not None
        return json.loads(rendered)

    def test_messages_and_payload_merge_into_one_document(self):
        buffer_agent_message("created thing", level="success")
        buffer_agent_payload({"rid": "ri.a"}, meta={"result_type": "dict"})
        envelope = self._flush()
        assert envelope["data"] == {"rid": "ri.a"}
        assert envelope["meta"]["messages"] == [
            {"level": "success", "message": "created thing"}
        ]

    def test_two_payloads_become_a_list(self):
        buffer_agent_payload({"n": 1})
        buffer_agent_payload({"n": 2})
        assert self._flush()["data"] == [{"n": 1}, {"n": 2}]

    def test_message_only_still_emits_an_envelope(self):
        buffer_agent_message("done", level="success")
        envelope = self._flush()
        assert envelope["data"] is None
        assert envelope["meta"]["messages"][0]["message"] == "done"

    def test_warning_and_error_levels_reach_their_envelope_fields(self):
        buffer_agent_message("careful", level="warning")
        buffer_agent_message("broke", level="error")
        envelope = self._flush()
        assert "careful" in envelope["warnings"]
        assert {"type": "error", "message": "broke"} in envelope["errors"]

    def test_nothing_buffered_emits_nothing(self):
        assert flush_agent_output(stream=_Sink()) is None

    def test_envelope_shape_is_unchanged(self):
        """The published contract keys are additive-only."""
        assert set(agent_envelope()) == ENVELOPE_KEYS
