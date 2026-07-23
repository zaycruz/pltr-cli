"""Tests for the machine-readable CLI grammar manifest."""

import json

import click
from typer.testing import CliRunner

from pltr.cli import app
from pltr.commands.agent_manifest import build_manifest


runner = CliRunner()


def _manifest(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_manifest_contains_registered_commands_and_flags() -> None:
    payload = _manifest(runner.invoke(app, ["agent-manifest"]))

    assert set(payload) == {"schemaVersion", "generatedAt", "commands"}
    assert payload["schemaVersion"] == "pltr-cli-command-manifest-v1"
    commands = {command["path"]: command for command in payload["commands"]}

    object_type_list = commands["ontology object-type-list"]
    assert "ontology_rid" in object_type_list["args"]
    assert "--format" in object_type_list["flags"]

    dataset_files_list = commands["dataset files list"]
    assert "dataset_rid" in dataset_files_list["args"]
    assert {"--all", "--format", "--page-size"} <= set(dataset_files_list["flags"])


def test_hidden_commands_are_excluded_recursively() -> None:
    root = click.Group(
        commands={
            "visible": click.Command("visible", help="Visible command"),
            "hidden": click.Command("hidden", hidden=True),
            "nested": click.Group(
                commands={
                    "visible": click.Command("visible", help="Nested command"),
                    "hidden": click.Command("hidden", hidden=True),
                }
            ),
        }
    )

    payload = build_manifest(root, generated_at="fixed")

    assert [command["path"] for command in payload["commands"]] == [
        "nested visible",
        "visible",
    ]


def test_manifest_is_stable_except_for_generated_at() -> None:
    first = _manifest(runner.invoke(app, ["agent-manifest"]))
    second = _manifest(runner.invoke(app, ["agent-manifest"]))

    first.pop("generatedAt")
    second.pop("generatedAt")
    assert first == second


def test_manifest_honors_the_shared_agent_output_contract() -> None:
    result = runner.invoke(app, ["--agent", "agent-manifest"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "pltr-agent-v1"
    assert payload["data"]["schemaVersion"] == "pltr-cli-command-manifest-v1"


def test_manifest_emission_failure_exits_nonzero(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("serialization failed")

    monkeypatch.setattr("pltr.commands.agent_manifest.render_manifest", fail)

    result = runner.invoke(app, ["agent-manifest"])

    assert result.exit_code == 1
    assert "Error emitting agent manifest: serialization failed" in result.output
