import csv
import hashlib
import json
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from pltr.cli import app
from pltr.commands import dependency as dependency_command
from pltr.services.dependency import DependencyFatalError
from pltr.utils.dependency_artifacts import (
    ArtifactWriteError,
    artifact_identity,
    default_artifact_path,
    write_dependency_artifact,
)
from pltr.utils.formatting import ProtectedOutputCollisionError


runner = CliRunner()


def independent_payload_digest(document):
    payload = {key: value for key, value in document.items() if key != "artifact"}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def analysis_result():
    return {
        "target": {"id": "object:Employee", "kind": "object-type", "display_name": "Employee"},
        "read_contexts": [
            {
                "id": "ctx",
                "profile": "qa",
                "host_fingerprint": "f9137f6af01050c3",
                "requested_branch": "dev",
            }
        ],
        "graph": {
            "nodes": [{"id": "object:Employee", "kind": "object-type"}],
            "edges": [{"id": "edge-1", "source": "query:q", "target": "object:Employee"}],
        },
        "paths": [
            {
                "id": "path-1",
                "direction": "upstream",
                "readable_path": "Employee <- Query q",
                "evidence_locator": "output",
                "sdk_namespace": "client.ontologies.Ontology.QueryType",
                "sdk_method": "list",
            }
        ],
        "coverage_records": [{"surface": "query-metadata", "status": "covered"}],
        "gaps": [
            {
                "surface": "schedule-reverse-index",
                "coverage": "partial",
                "reason_code": "schedule-index-may-be-stale",
                "message": "Schedule index may be stale for up to one hour.",
            }
        ],
        "errors": [
            {
                "id": "error-1",
                "error_class": "timeout",
                "message": "One optional reverse lookup timed out.",
            }
        ],
        "evidence": [{"id": "ev-1", "operation_provenance_id": "op-1", "locator": "output"}],
        "operation_provenance": [
            {
                "id": "op-1",
                "sdk_namespace": "client.ontologies.Ontology.QueryType",
                "sdk_method": "list",
                "capability_ids": ["CAP-05", "CAP-16"],
                "invocation_sdk_version": "1.95.0",
                "branch_argument": {"mode": "explicit", "value": "dev"},
                "preview_argument": {"mode": "not-applicable", "value": None},
            },
            {
                "id": "op-2",
                "sdk_namespace": "client.filesystem.Resource",
                "sdk_method": "get",
                "capability_ids": ["CAP-16"],
                "invocation_sdk_version": "1.95.0",
                "branch_argument": {"mode": "not-applicable", "value": None},
                "preview_argument": {"mode": "not-applicable", "value": None},
            },
        ],
        "budget": {"used": {"requests": 2}, "limits": {"requests": 200}},
        "summary": {"assessment": "Partial dependency coverage."},
    }


@pytest.fixture
def service():
    with (
        patch("pltr.commands.dependency.AuthManager") as auth_constructor,
        patch("pltr.commands.dependency.DependencyGraphService") as constructor,
    ):
        auth_manager = auth_constructor.return_value
        auth_manager.get_current_profile.return_value = "active"
        auth_manager.storage.get_profile.side_effect = lambda profile: {
            "host": f"https://{profile}.example.com",
            "token": "must-not-enter-provenance",
        }
        instance = constructor.return_value
        context = object()
        target = object()
        instance.create_context.return_value = context
        for method_name in (
            "resolve_object_type",
            "resolve_property",
            "resolve_link_type",
            "resolve_action_type",
            "resolve_query_type",
            "resolve_resource",
        ):
            getattr(instance, method_name).return_value = target
        instance.analyze.return_value = analysis_result()
        yield constructor, instance, context, target


@pytest.mark.parametrize(
    ("arguments", "resolver", "resolver_arguments", "ontology_rid"),
    [
        (["object-type", "ri.ontology", "Employee"], "resolve_object_type", ("ri.ontology", "Employee"), "ri.ontology"),
        (["property", "ri.ontology", "Employee", "email"], "resolve_property", ("ri.ontology", "Employee", "email"), "ri.ontology"),
        (["link-type", "ri.ontology", "Employee", "manager"], "resolve_link_type", ("ri.ontology", "Employee", "manager"), "ri.ontology"),
        (["action-type", "ri.ontology", "promote"], "resolve_action_type", ("ri.ontology", "promote"), "ri.ontology"),
        (["query-type", "ri.ontology", "findEmployee"], "resolve_query_type", ("ri.ontology", "findEmployee"), "ri.ontology"),
        (["resource", "ri.foundry.main.dataset.abc"], "resolve_resource", ("ri.foundry.main.dataset.abc",), None),
    ],
)
def test_each_command_resolves_and_analyzes_once(
    tmp_path, service, arguments, resolver, resolver_arguments, ontology_rid
):
    constructor, instance, context, target = service
    graph_path = tmp_path / f"{resolver}.json"
    result = runner.invoke(
        app,
        [
            "dependency",
            *arguments,
            "--profile", "qa",
            "--branch", "dev",
            "--change", "rename field",
            "--direction", "downstream",
            "--depth", "4",
            "--max-nodes", "250",
            "--max-requests", "300",
            "--max-pages", "120",
            "--max-items", "12000",
            "--time-budget-seconds", "90",
            "--graph-output", str(graph_path),
        ],
    )
    assert result.exit_code == 0, result.output
    constructor.assert_called_once_with(profile="qa")
    create_kwargs = instance.create_context.call_args.kwargs
    assert create_kwargs["host"] == "https://qa.example.com"
    assert create_kwargs["ontology_rid"] == ontology_rid
    assert create_kwargs["requested_branch"] == "dev"
    assert create_kwargs["dataset_branch"] == "dev"
    budget = create_kwargs["budget"]
    assert budget.max_depth == 4
    assert budget.max_nodes == 250
    assert budget.max_requests == 300
    assert budget.max_pages == 120
    assert budget.max_items == 12000
    assert budget.time_budget_seconds == 90
    getattr(instance, resolver).assert_called_once_with(context, *resolver_arguments)
    instance.analyze.assert_called_once_with(
        target, context, direction="downstream", change="rename field"
    )
    assert graph_path.exists()


@pytest.mark.parametrize(
    ("option", "value"),
    [("--max-requests", "1001"), ("--max-pages", "501"), ("--max-items", "100001"), ("--max-nodes", "1001"), ("--depth", "11"), ("--time-budget-seconds", "601")],
)
def test_hard_ceiling_fails_before_service_construction(tmp_path, option, value):
    with patch("pltr.commands.dependency.DependencyGraphService") as constructor:
        result = runner.invoke(
            app,
            ["dependency", "resource", "ri.foundry.main.dataset.abc", option, value, "--graph-output", str(tmp_path / "graph.json")],
        )
    assert result.exit_code != 0
    assert "hard ceiling" in result.output
    constructor.assert_not_called()


def test_output_does_not_replace_graph_output_or_repeat_analysis(tmp_path, service):
    _, instance, _, _ = service
    rendered = tmp_path / "rendered.json"
    graph = tmp_path / "graph.json"
    result = runner.invoke(
        app,
        ["dependency", "resource", "ri.foundry.main.dataset.abc", "--format", "json", "--full", "--output", str(rendered), "--graph-output", str(graph)],
    )
    assert result.exit_code == 0, result.output
    assert rendered.exists() and graph.exists()
    assert rendered != graph
    rendered_document = json.loads(rendered.read_text())
    artifact_document = json.loads(graph.read_text())
    assert rendered_document == artifact_document
    assert {
        key: value for key, value in artifact_document.items() if key != "artifact"
    } == analysis_result()
    artifact = artifact_document["artifact"]
    expected_digest = independent_payload_digest(artifact_document)
    assert artifact["path"] == str(graph.resolve())
    assert artifact["sha256"] == expected_digest
    assert artifact["analysis_id"] == f"dep-{expected_digest[:20]}"
    instance.analyze.assert_called_once()


@pytest.mark.parametrize("alias_kind", ["equal", "canonical", "symlink"])
def test_output_alias_collision_is_rejected_before_analysis_or_writes(
    tmp_path, alias_kind
):
    graph = tmp_path / "graph.json"
    if alias_kind == "equal":
        output = graph
    elif alias_kind == "canonical":
        (tmp_path / "nested").mkdir()
        output = tmp_path / "nested" / ".." / "graph.json"
    else:
        output = tmp_path / "rendered.json"
        output.symlink_to(graph)

    with (
        patch("pltr.commands.dependency.AuthManager") as auth_constructor,
        patch("pltr.commands.dependency.DependencyGraphService") as constructor,
    ):
        result = runner.invoke(
            app,
            [
                "dependency",
                "resource",
                "ri.foundry.main.dataset.abc",
                "--output",
                str(output),
                "--graph-output",
                str(graph),
            ],
        )

    assert result.exit_code != 0
    assert "rendered output cannot replace the graph artifact" in result.output
    auth_constructor.assert_not_called()
    constructor.assert_not_called()
    assert not graph.exists()


def test_nonexistent_filesystem_equivalent_alias_is_rechecked_after_artifact_write(
    tmp_path, service
):
    graph = tmp_path / "Graph.json"
    output = tmp_path / "graph.json"

    def filesystem_equivalent_after_creation(first, second):
        assert {Path(first).name.casefold(), Path(second).name.casefold()} == {
            "graph.json"
        }
        return graph.exists()

    with (
        patch(
            "pltr.commands.dependency.os.path.samefile",
            side_effect=filesystem_equivalent_after_creation,
        ),
        patch.object(
            dependency_command.formatter,
            "format_dependency_result",
        ) as render,
    ):
        result = runner.invoke(
            app,
            [
                "dependency",
                "resource",
                "ri.foundry.main.dataset.abc",
                "--output",
                str(output),
                "--graph-output",
                str(graph),
            ],
        )

    assert result.exit_code != 0
    assert "rendered output cannot replace the graph artifact" in result.output
    assert json.loads(graph.read_text())["artifact"]["path"] == str(graph.resolve())
    render.assert_not_called()


def test_renderer_inode_guard_refuses_to_truncate_artifact(tmp_path):
    graph = tmp_path / "graph.json"
    rendered = tmp_path / "rendered.json"
    document = analysis_result()
    document["artifact"] = write_dependency_artifact(document, graph)
    rendered.hardlink_to(graph)
    original = graph.read_bytes()

    with pytest.raises(
        ProtectedOutputCollisionError,
        match="rendered output cannot replace the graph artifact",
    ):
        dependency_command.formatter.format_dependency_result(
            document,
            format_type="json",
            output_file=str(rendered),
            protected_output_file=str(graph),
        )

    retained = json.loads(graph.read_text())
    assert graph.read_bytes() == original
    assert rendered.read_bytes() == original
    assert retained["artifact"]["sha256"] == independent_payload_digest(retained)


def test_active_profile_and_non_secret_host_are_resolved_once(tmp_path):
    graph = tmp_path / "graph.json"
    with (
        patch("pltr.commands.dependency.AuthManager") as auth_constructor,
        patch("pltr.commands.dependency.DependencyGraphService") as constructor,
    ):
        auth_manager = auth_constructor.return_value
        auth_manager.get_current_profile.return_value = "prod"
        auth_manager.storage.get_profile.return_value = {
            "host": "https://prod.example.com/",
            "token": "secret-token",
            "client_secret": "secret-client",
        }
        service_instance = constructor.return_value
        service_instance.create_context.return_value = object()
        service_instance.resolve_resource.return_value = object()
        service_instance.analyze.return_value = analysis_result()

        result = runner.invoke(
            app,
            [
                "dependency",
                "resource",
                "ri.foundry.main.dataset.abc",
                "--graph-output",
                str(graph),
            ],
        )

    assert result.exit_code == 0, result.output
    auth_manager.get_current_profile.assert_called_once_with()
    auth_manager.storage.get_profile.assert_called_once_with("prod")
    constructor.assert_called_once_with(profile="prod")
    create_kwargs = service_instance.create_context.call_args.kwargs
    assert create_kwargs["host"] == "https://prod.example.com/"
    assert "secret-token" not in repr(create_kwargs)
    assert "secret-client" not in repr(create_kwargs)


def test_output_cannot_alias_computed_default_graph_path(
    tmp_path, monkeypatch, service
):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    analysis_id, _ = artifact_identity(analysis_result())
    graph = default_artifact_path(analysis_id)

    result = runner.invoke(
        app,
        [
            "dependency",
            "resource",
            "ri.foundry.main.dataset.abc",
            "--output",
            str(graph),
        ],
    )

    assert result.exit_code != 0
    assert "rendered output cannot replace the graph artifact" in result.output
    assert not graph.exists()


def test_csv_has_every_explicit_row_kind(tmp_path, service):
    rendered = tmp_path / "rendered.csv"
    result = runner.invoke(
        app,
        ["dependency", "resource", "ri.foundry.main.dataset.abc", "--format", "csv", "--output", str(rendered), "--graph-output", str(tmp_path / "graph.json")],
    )
    assert result.exit_code == 0, result.output
    kinds = {row["row_kind"] for row in csv.DictReader(StringIO(rendered.read_text()))}
    assert kinds == {
        "artifact",
        "read-context",
        "node",
        "edge",
        "path",
        "coverage",
        "gap",
        "error",
        "evidence",
        "operation-provenance",
    }
    rows = list(csv.DictReader(StringIO(rendered.read_text())))
    artifact_row = next(row for row in rows if row["row_kind"] == "artifact")
    read_context_row = next(row for row in rows if row["row_kind"] == "read-context")
    artifact = json.loads(artifact_row["record"])
    read_context = json.loads(read_context_row["record"])
    assert artifact_row["id"] == artifact["analysis_id"]
    assert artifact["path"] == str((tmp_path / "graph.json").resolve())
    assert artifact["sha256"]
    assert read_context_row["id"] == "ctx"
    assert read_context["profile"] == "qa"
    assert read_context["host_fingerprint"] == "f9137f6af01050c3"


def test_partial_gap_exits_zero_and_table_references_complete_artifact(tmp_path, service):
    result = runner.invoke(
        app,
        ["dependency", "resource", "ri.foundry.main.dataset.abc", "--graph-output", str(tmp_path / "graph.json")],
    )
    assert result.exit_code == 0
    assert "schedule-index-may-be-stale" in result.output
    assert "up to one hour" in result.output
    assert "Graph artifact:" in result.output
    assert "SHA-256:" in result.output
    assert "Employee <- Query q" in result.output
    assert "output" in result.output
    assert "client.ontologies.Ontology.QueryType.list" in result.output


def test_json_preserves_branchless_operation_provenance(tmp_path, service):
    result = runner.invoke(
        app,
        ["dependency", "resource", "ri.foundry.main.dataset.abc", "--branch", "dev", "--format", "json", "--graph-output", str(tmp_path / "graph.json")],
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    branchless = document["operation_provenance"][1]
    assert branchless["branch_argument"] == {"mode": "not-applicable", "value": None}


def test_only_six_direct_target_commands_are_advertised():
    result = runner.invoke(app, ["dependency", "--help"])
    assert result.exit_code == 0
    for command in ("object-type", "property", "link-type", "action-type", "query-type", "resource"):
        assert command in result.output
    for excluded in ("function", "schedule", "workshop-variable"):
        assert f"  {excluded} " not in result.output


def test_resource_rejects_workshop_name_before_service_construction():
    with patch("pltr.commands.dependency.DependencyGraphService") as constructor:
        result = runner.invoke(app, ["dependency", "resource", "my-workshop-variable"])
    assert result.exit_code != 0
    assert "Compass-resolvable RID" in result.output
    constructor.assert_not_called()


def test_fatal_error_is_stable_json_without_artifact(tmp_path, service):
    _, instance, _, _ = service
    instance.resolve_resource.side_effect = DependencyFatalError(
        "permission-denied",
        "ri.foundry.main.dataset.secret",
        "filesystem.resource.get",
        "Access denied",
        False,
        "ctx-1",
    )
    graph = tmp_path / "graph.json"
    result = runner.invoke(
        app,
        ["dependency", "resource", "ri.foundry.main.dataset.secret", "--format", "json", "--graph-output", str(graph)],
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error_class"] == "permission-denied"
    assert not graph.exists()
    instance.analyze.assert_not_called()


def test_unknown_exception_is_structured_without_traceback(tmp_path, service):
    _, instance, _, _ = service
    instance.analyze.side_effect = RuntimeError("unexpected")
    result = runner.invoke(
        app,
        ["dependency", "resource", "ri.foundry.main.dataset.abc", "--format", "json", "--graph-output", str(tmp_path / "graph.json")],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error_class"] == "unknown"
    assert payload["message"] == "unexpected"
    assert "Traceback" not in result.output


def test_artifact_failure_prevents_compact_success_output(tmp_path, service):
    with patch(
        "pltr.commands.dependency.write_dependency_artifact",
        side_effect=ArtifactWriteError("read only"),
    ):
        result = runner.invoke(
            app,
            ["dependency", "resource", "ri.foundry.main.dataset.abc", "--graph-output", str(tmp_path / "graph.json")],
        )
    assert result.exit_code == 1
    assert "artifact-write-failed" in result.output
    assert "Dependency analysis\n" not in result.output
