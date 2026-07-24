"""Emit a machine-readable manifest of the registered CLI commands."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Iterator, Mapping

import click
import typer

from ..utils.agent_output import (
    agent_mode_enabled,
    buffer_agent_payload,
    render_agent_json,
)


GRAMMAR_MANIFEST_SCHEMA_VERSION = "pltr-cli-command-manifest-v1"

app = typer.Typer()


def _parameter_names(parameter: click.Parameter) -> list[str]:
    """Return all declared option spellings in declaration order."""
    return [
        *getattr(parameter, "opts", []),
        *getattr(parameter, "secondary_opts", []),
    ]


def _iter_leaf_commands(
    command: click.Command, prefix: tuple[str, ...] = ()
) -> Iterator[tuple[str, click.Command]]:
    """Yield visible, executable leaf commands from a Click command tree."""
    if getattr(command, "hidden", False):
        return

    if isinstance(command, click.Group):
        children = [
            (name, child)
            for name, child in sorted(command.commands.items())
            if not getattr(child, "hidden", False)
        ]
        if children:
            for name, child in children:
                yield from _iter_leaf_commands(child, (*prefix, name))
            return

    if prefix:
        yield " ".join(prefix), command


def _command_manifest(path: str, command: click.Command) -> dict[str, Any]:
    """Build the stable grammar record for one executable command."""
    args: list[str] = []
    flags: list[str] = []
    for parameter in command.params:
        if isinstance(parameter, click.Option):
            flags.extend(_parameter_names(parameter))
        elif isinstance(parameter, click.Argument) and parameter.name is not None:
            args.append(parameter.name)

    return {
        "path": path,
        "description": command.help or "",
        "args": args,
        "flags": flags,
    }


def build_manifest(
    root_command: click.Command,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic manifest from the converted Click command tree."""
    commands = [
        _command_manifest(path, command)
        for path, command in _iter_leaf_commands(root_command)
    ]
    commands.sort(key=lambda item: item["path"])

    return {
        "schemaVersion": GRAMMAR_MANIFEST_SCHEMA_VERSION,
        "generatedAt": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "commands": commands,
    }


def render_manifest(manifest: Mapping[str, Any], *, agent: bool = False) -> str:
    """Serialize a manifest using either direct or the shared agent contract."""
    if agent:
        return render_agent_json(manifest, meta={"result_type": "grammar_manifest"})
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


@app.callback(invoke_without_command=True)
def agent_manifest(ctx: typer.Context) -> None:
    """Emit every registered command as deterministic JSON -- the command surface.

    This is the authoritative list of what the CLI can do: each entry carries a
    command path, its arguments and its flags. `pltr capabilities` is a
    different view -- it scores that surface against Palantir's MCP tool catalog.
    """
    try:
        manifest = build_manifest(ctx.find_root().command)
        if agent_mode_enabled():
            buffer_agent_payload(manifest, meta={"result_type": "grammar_manifest"})
        else:
            typer.echo(render_manifest(manifest), nl=False)
    except Exception as error:
        typer.echo(f"Error emitting agent manifest: {error}", err=True)
        raise typer.Exit(1) from error
