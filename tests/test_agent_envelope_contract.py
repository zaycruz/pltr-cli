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
        # A group that runs without a subcommand (`pltr capabilities`) is an
        # invocable command in its own right. Walking only leaves let one
        # escape this contract entirely.
        if path and command.invoke_without_command:
            yield path
        for name, sub in command.commands.items():
            yield from _walk(sub, path + (name,))
    elif path:
        yield path


def all_command_paths() -> List[Tuple[str, ...]]:
    return sorted(_walk(get_command(app)))


def _placeholder(param: click.Parameter) -> List[str]:
    """Invent a value that satisfies one required parameter.

    Without this, every command that takes a required argument exits 2 on a
    usage error before its body runs, and the contract measures nothing.
    """
    name = (param.name or "").lower()
    if isinstance(param.type, click.Choice):
        value = str(param.type.choices[0])
    elif isinstance(param.type, click.types.IntParamType):
        value = "1"
    elif "rid" in name:
        value = "ri.foundry.main.dataset.placeholder"
    elif "path" in name or "file" in name:
        value = "placeholder"
    else:
        value = "placeholder"
    if param.param_type_name == "argument":
        return [value] * (param.nargs if param.nargs > 0 else 1)
    return [param.opts[0], value]


def invocation_args(path: Tuple[str, ...]) -> List[str]:
    """argv that gets past Click's parser and into the command body."""
    command = get_command(app)
    for part in path:
        command = command.commands[part]  # type: ignore[attr-defined]
    args: List[str] = []
    for param in command.params:
        if not param.required:
            continue
        if isinstance(param, click.Option) and param.is_flag:
            args.append(param.opts[0])
            continue
        args.extend(_placeholder(param))
    return args


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
    argv = ["--agent", *path, *invocation_args(path)]
    with patch("pltr.auth.storage.CredentialStorage", MagicMock()):
        result = runner.invoke(app, argv, catch_exceptions=True)

    stdout = result.stdout
    if not stdout.strip():
        # Exit 2 with nothing on stdout means Click rejected the argv before
        # the body ran -- a parser-level usage error this harness could not
        # satisfy, reported on stderr. Anything else owes the caller an
        # envelope: treating empty stdout as compliant is how commands went
        # silent unnoticed.
        assert result.exit_code == 2, (
            f"`pltr {' '.join(argv)}` exited {result.exit_code} but wrote "
            "nothing to stdout; an agent has no result to read"
        )
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


def test_most_commands_reach_their_body():
    """Guard the guard: a harness that only trips usage errors proves nothing."""
    reached = 0
    unreachable = []
    for path in all_command_paths():
        if path[0] in NOT_INVOCABLE:
            continue
        argv = ["--agent", *path, *invocation_args(path)]
        with patch("pltr.auth.storage.CredentialStorage", MagicMock()):
            result = runner.invoke(app, argv, catch_exceptions=True)
        if result.stdout.strip():
            reached += 1
        else:
            unreachable.append(" ".join(path))

    total = reached + len(unreachable)
    assert reached / total > 0.9, (
        f"only {reached}/{total} commands produced an envelope; the contract "
        f"is not measuring the real surface. Unreached: {unreachable[:15]}"
    )


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


class TestAdvertisedBypassFlagsExist:
    """A refusal that names a nonexistent flag is worse than no refusal.

    `require_confirmation(..., option_name=X)` tells an agent exactly what to
    pass. Six sites advertised `--confirm` on commands that declare `--yes`,
    so following the instruction produced a usage error.
    """

    @staticmethod
    def _declared_flags() -> dict:
        import click
        from typer.main import get_command
        from pltr.cli import app

        flags: dict = {}

        def walk(cmd):
            if isinstance(cmd, click.Group):
                for sub in cmd.commands.values():
                    walk(sub)
            if getattr(cmd, "callback", None) is not None:
                declared = set()
                for param in cmd.params:
                    # secondary_opts carries the `--no-x` half of a bool flag
                    declared.update(param.opts)
                    declared.update(getattr(param, "secondary_opts", []))
                flags.setdefault(cmd.callback.__name__, set()).update(declared)

        walk(get_command(app))
        return flags

    @staticmethod
    def _advertised_flags():
        import ast
        import pathlib

        sites = []
        for path in sorted(pathlib.Path("src/pltr/commands").glob("*.py")):
            tree = ast.parse(path.read_text())
            for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                for node in ast.walk(fn):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "require_confirmation"
                    ):
                        option = next(
                            (
                                kw.value.value
                                for kw in node.keywords
                                if kw.arg == "option_name"
                                and isinstance(kw.value, ast.Constant)
                            ),
                            # matches require_confirmation's own default
                            "--force",
                        )
                        sites.append((path.name, fn.name, option))
        return sites

    def test_every_site_names_a_flag_its_command_declares(self):
        declared = self._declared_flags()
        sites = self._advertised_flags()
        assert sites, "no require_confirmation sites found; the walk is broken"

        wrong = [
            f"{filename}:{func} advertises {option} but the command declares "
            f"{sorted(declared.get(func, set()))}"
            for filename, func, option in sites
            if option not in declared.get(func, set())
        ]
        assert not wrong, (
            "confirmation refusals name flags that do not exist:\n" + "\n".join(wrong)
        )
