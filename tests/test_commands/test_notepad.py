"""CLI tests for read-only notepad output and fail-safe exits."""

from __future__ import annotations

import csv
import json
from io import StringIO
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pltr.cli import app
from pltr.services.foundry_internal_client import TokenExpiredError
from pltr.services.notepad import NOTEPAD_TYPE_NAME


runner = CliRunner()


def test_readable_notepad_renders_body_and_reference_identifiers():
    payload = {
        "status": "readable",
        "reason": None,
        "rid": "ri.notepad.1",
        "name": "Agent notes",
        "version": 7,
        "metadata": {"name": "Agent notes"},
        "body": [],
        "body_text": "Important prose",
        "references": [
            {
                "kind": "compass-resource-section-plugin",
                "section_type_id": (
                    "rich-text-editor.section.v1.compass-resource-section-plugin"
                ),
                "config": {"rid": "ri.foundry.main.dataset.example"},
            },
            {
                "kind": "stored-image-custom-section-plugin",
                "section_type_id": (
                    "rich-text-editor.section.v1.stored-image-custom-section-plugin"
                ),
                "config": {"name": "chart.png", "snapshotRid": "ri.snapshot.1"},
            },
        ],
    }
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.get.return_value = payload
        result = runner.invoke(
            app,
            [
                "notepad",
                "get",
                "ri.notepad.1",
                "--profile",
                "qa",
                "--output-mode",
                "agent",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "NOTEPAD READ [readable]" in result.output
    assert "Agent notes" in result.output
    assert "Important prose" in result.output
    assert "rid=ri.foundry.main.dataset.example" in result.output
    assert "snapshotRid=ri.snapshot.1" in result.output
    service.assert_called_once_with(profile="qa")
    service.return_value.get.assert_called_once_with("ri.notepad.1")


def test_notepad_null_inconclusive_exits_nonzero_and_warns_not_empty():
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.get.return_value = {
            "status": "inconclusive",
            "reason": "notepad-null",
            "body": None,
            "body_text": None,
            "references": None,
        }
        result = runner.invoke(app, ["notepad", "get", "ri.notepad.missing"])

    assert result.exit_code == 1
    assert "INCONCLUSIVE: notepad-null" in result.output
    assert "NOT proof the notepad is empty" in result.output
    assert "EMPTY DOCUMENT" not in result.output


def test_empty_document_is_successful_and_plainly_identified():
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.get.return_value = {
            "status": "empty-document",
            "reason": None,
            "rid": "ri.notepad.empty",
            "name": "Empty",
            "version": 1,
            "body": [],
            "body_text": "",
            "references": [],
        }
        result = runner.invoke(app, ["notepad", "get", "ri.notepad.empty"])

    assert result.exit_code == 0, result.output
    assert "EMPTY DOCUMENT" in result.output
    assert "INCONCLUSIVE" not in result.output


def test_token_expired_shows_degraded_banner_and_exits_nonzero():
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.get.side_effect = TokenExpiredError("expired")
        result = runner.invoke(app, ["notepad", "get", "ri.notepad.1"])

    assert result.exit_code == 1
    assert "DEGRADED [token-expired]" in result.output
    assert "re-authenticate" in result.output


def test_inconclusive_json_and_csv_preserve_machine_format_and_warning():
    payload = {
        "status": "inconclusive",
        "reason": "contents-null",
        "body": None,
        "body_text": None,
        "references": None,
    }
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.get.return_value = payload
        json_result = runner.invoke(
            app, ["notepad", "get", "ri.notepad.1", "--format", "json"]
        )
        csv_result = runner.invoke(
            app, ["notepad", "get", "ri.notepad.1", "--format", "csv"]
        )

    assert json_result.exit_code == 1
    assert csv_result.exit_code == 1
    assert json.loads(json_result.output)["warning"].startswith(
        "INCONCLUSIVE: contents-null"
    )
    rows = list(csv.DictReader(StringIO(csv_result.output)))
    assert rows[0]["warning"].startswith("INCONCLUSIVE: contents-null")


def _list_payload() -> dict:
    return {
        "mode": "notepad-list",
        "status": "ok",
        "reason": None,
        "server_filter": {"pathStartsWith": ["/Team"]},
        "local_filters": {
            "text": None,
            "resource_type": NOTEPAD_TYPE_NAME,
            "scope": "returned-page-only",
        },
        "relationship_claim": None,
        "page_size": 25,
        "page_token": None,
        "next_page_token": "next-notepads",
        "coverage": "partial",
        "truncated": True,
        "server_page_count": 2,
        "coverage_note": (
            "pathStartsWith is the only verified server-side filter; text and "
            "resource type constraints are applied locally to this returned page"
        ),
        "results": [
            {
                "rid": "ri.notepad.1",
                "name": "Flight Notes",
                "path": "/Team/Flight Notes",
                "type": "NoTePaD DoCUmEnT",
                "highlights": [{"field": "path", "matches": ["/Team/Flight Notes"]}],
            },
            {
                "rid": "ri.notepad.2",
                "name": "Planning",
                "path": "/Team/Planning",
                "type": "notepad document",
                "highlights": [],
            },
        ],
    }


def test_notepad_list_requires_path_prefix_instead_of_guessing_root():
    with patch("pltr.commands.notepad.NotepadService") as service:
        result = runner.invoke(app, ["notepad", "list"])

    assert result.exit_code == 2
    assert "instance root is not guessed" in result.output
    service.return_value.list.assert_not_called()


def test_notepad_list_agent_output_preserves_partial_page_signals():
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.list.return_value = _list_payload()
        result = runner.invoke(
            app,
            [
                "notepad",
                "list",
                "--path-prefix",
                "/Team",
                "--page-size",
                "25",
                "--profile",
                "qa",
                "--output-mode",
                "agent",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "NOTEPAD LIST [ok]" in result.output
    assert "Flight Notes" in result.output
    assert "Planning" in result.output
    assert "Coverage: partial" in result.output
    assert "next-notepads" in result.output
    service.assert_called_once_with(profile="qa")
    service.return_value.list.assert_called_once_with(
        ["/Team"], page_size=25, page_token=None
    )


def test_notepad_list_text_filter_is_local_and_json_keeps_coverage():
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.list.return_value = _list_payload()
        result = runner.invoke(
            app,
            [
                "notepad",
                "list",
                "--path-prefix",
                "/Team",
                "--text",
                "flight",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [resource["name"] for resource in payload["results"]] == ["Flight Notes"]
    assert payload["local_filters"]["scope"] == "returned-page-only"
    assert payload["local_filters"]["resource_type"] == NOTEPAD_TYPE_NAME
    assert payload["coverage"] == "partial"
    assert payload["next_page_token"] == "next-notepads"
    assert payload["relationship_claim"] is None


def test_notepad_list_inconclusive_and_ci_outputs_are_machine_readable():
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.list.return_value = {
            "mode": "notepad-list",
            "status": "inconclusive",
            "reason": "search-resources-null",
            "results": None,
        }
        json_result = runner.invoke(
            app,
            [
                "notepad",
                "list",
                "--path-prefix",
                "/Team",
                "--format",
                "json",
            ],
        )
        ci_result = runner.invoke(
            app,
            [
                "notepad",
                "list",
                "--path-prefix",
                "/Team",
                "--output-mode",
                "ci",
            ],
        )

    assert json_result.exit_code == 1
    assert json.loads(json_result.output)["status"] == "inconclusive"
    assert ci_result.exit_code == 2
    assert json.loads(ci_result.output)["reason"] == "search-resources-null"


def test_notepad_list_is_registered_without_cli_module_edits():
    result = runner.invoke(app, ["notepad", "--help"])

    assert result.exit_code == 0, result.output
    assert "list" in result.output
    assert "get" in result.output


@pytest.mark.parametrize("page_size", [0, 501])
def test_notepad_list_rejects_out_of_bounds_page_size(page_size):
    with patch("pltr.commands.notepad.NotepadService") as service:
        result = runner.invoke(
            app,
            [
                "notepad",
                "list",
                "--path-prefix",
                "/Team",
                "--page-size",
                str(page_size),
            ],
        )

    assert result.exit_code == 2
    assert "between 1 and 500" in result.output
    service.return_value.list.assert_not_called()


@pytest.mark.parametrize(
    ("coverage", "next_page_token", "absence_text"),
    [
        ("complete", None, "path-scoped result"),
        ("partial", "next-empty", "not a stack-wide absence claim"),
    ],
)
def test_notepad_list_empty_pages_render_coverage(
    coverage, next_page_token, absence_text
):
    payload = _list_payload()
    payload.update(
        {
            "coverage": coverage,
            "next_page_token": next_page_token,
            "server_page_count": 0,
            "results": [],
        }
    )
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.list.return_value = payload
        result = runner.invoke(
            app,
            ["notepad", "list", "--path-prefix", "/Team"],
        )

    assert result.exit_code == 0, result.output
    assert f"Coverage: {coverage}" in result.output
    assert f"Next page token: {next_page_token or 'none'}" in result.output
    assert absence_text in result.output


def test_notepad_reference_table_treats_malformed_brackets_as_literal():
    payload = {
        "status": "readable",
        "reason": None,
        "rid": "ri.notepad.1",
        "name": "[Notes]",
        "version": 1,
        "body": [],
        "body_text": "body\x1b]0;bad\x07",
        "references": [
            {
                "kind": "[broken",
                "section_type_id": "[link](target)",
                "config": {"rid": "ri.[literal]"},
            }
        ],
    }
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.get.return_value = payload
        result = runner.invoke(app, ["notepad", "get", "ri.notepad.1"])

    assert result.exit_code == 0, result.output
    assert "[broken" in result.output
    assert "[link](target)" in result.output
    assert "ri.[literal]" in result.output
    assert "\x1b" not in result.output
    assert "\x07" not in result.output


def test_notepad_csv_neutralizes_every_server_formula_cell():
    payload = {
        "status": "readable",
        "reason": None,
        "rid": "ri.notepad.1",
        "name": "  =NAME()",
        "version": 1,
        "body": [],
        "body_text": "+body",
        "references": [
            {
                "kind": "-kind",
                "section_type_id": "@section",
                "config": {"rid": "=rid"},
            }
        ],
    }
    with patch("pltr.commands.notepad.NotepadService") as service:
        service.return_value.get.return_value = payload
        result = runner.invoke(
            app,
            ["notepad", "get", "ri.notepad.1", "--format", "csv"],
        )

    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(StringIO(result.output)))
    assert rows[0]["name"] == "'  =NAME()"
    assert rows[0]["body_text"] == "'+body"
    assert rows[1]["kind"] == "'-kind"
    assert rows[1]["section_type_id"] == "'@section"
