"""CLI tests for read-only cross-resource search output and fail-safe exits."""

from __future__ import annotations

import csv
import json
from io import StringIO
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pltr.cli import app
from pltr.services.foundry_internal_client import TokenExpiredError


runner = CliRunner()


def _ok_payload() -> dict:
    return {
        "status": "ok",
        "reason": None,
        "query": "Flight",
        "limit": 25,
        "truncation_note": "search(title:) has no page token; ...",
        "results": [
            {
                "rid": "ri.foundry.main.dataset.1",
                "name": "Flights",
                "path": "/AIP Now Ontology/Data/Flights",
                "type": "Dataset",
                "typename": "ResourceMetadata",
            },
            {
                "rid": "ri.workshop.main.module.2",
                "name": "Flights Map",
                "path": "/Apps/Flights Map",
                "type": "Module",
                "typename": "ResourceMetadata",
            },
        ],
    }


def test_search_renders_results_table_and_notes_truncation():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = _ok_payload()
        result = runner.invoke(
            app,
            ["search", "Flight", "--profile", "qa", "--output-mode", "agent"],
        )

    assert result.exit_code == 0, result.output
    assert "SEARCH [ok]" in result.output
    assert "Flights" in result.output
    assert "ri.workshop.main.module.2" in result.output
    assert "no page token" in result.output
    service.assert_called_once_with(profile="qa")
    service.return_value.search.assert_called_once_with("Flight", limit=25)


def test_search_limit_option_is_passed_through():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = _ok_payload()
        result = runner.invoke(app, ["search", "Flight", "--limit", "5"])

    assert result.exit_code == 0, result.output
    service.return_value.search.assert_called_once_with("Flight", limit=5)


def test_search_json_output_is_machine_readable():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = _ok_payload()
        result = runner.invoke(app, ["search", "Flight", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["results"][0]["rid"] == "ri.foundry.main.dataset.1"


def test_search_csv_output_rows():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = _ok_payload()
        result = runner.invoke(app, ["search", "Flight", "--format", "csv"])

    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(StringIO(result.output)))
    assert len(rows) == 2
    assert list(rows[0]) == [
        "status",
        "query",
        "limit",
        "rid",
        "name",
        "type",
        "path",
        "reason",
        "warning",
    ]
    assert rows[0]["name"] == "Flights"
    assert rows[1]["type"] == "Module"


def test_empty_results_is_successful_and_plainly_identified():
    payload = _ok_payload()
    payload["results"] = []
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = payload
        result = runner.invoke(app, ["search", "zzz-nothing"])

    assert result.exit_code == 0, result.output
    assert "No results" in result.output
    assert "INCONCLUSIVE" not in result.output


def test_inconclusive_exits_nonzero_and_warns_not_empty():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = {
            "status": "inconclusive",
            "reason": "search-null",
            "results": None,
        }
        result = runner.invoke(app, ["search", "Flight"])

    assert result.exit_code == 1
    assert "INCONCLUSIVE: search-null" in result.output
    assert "NOT proof there are no matching resources" in result.output


def test_inconclusive_json_and_csv_preserve_machine_format_and_warning():
    payload = {
        "status": "inconclusive",
        "reason": "missing-response-frame",
        "results": None,
    }
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = payload
        json_result = runner.invoke(app, ["search", "Flight", "--format", "json"])
        csv_result = runner.invoke(app, ["search", "Flight", "--format", "csv"])

    assert json_result.exit_code == 1
    assert csv_result.exit_code == 1
    assert json.loads(json_result.output)["warning"].startswith(
        "INCONCLUSIVE: missing-response-frame"
    )
    rows = list(csv.DictReader(StringIO(csv_result.output)))
    assert rows[0]["warning"].startswith("INCONCLUSIVE: missing-response-frame")


def test_token_expired_shows_degraded_banner_and_exits_nonzero():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.side_effect = TokenExpiredError("expired")
        result = runner.invoke(app, ["search", "Flight"])

    assert result.exit_code == 1
    assert "DEGRADED [token-expired]" in result.output
    assert "re-authenticate" in result.output


def test_invalid_format_and_limit_are_rejected():
    with patch("pltr.commands.search.SearchService"):
        bad_format = runner.invoke(app, ["search", "Flight", "--format", "yaml"])
        bad_limit = runner.invoke(app, ["search", "Flight", "--limit", "0"])

    assert bad_format.exit_code != 0
    assert bad_limit.exit_code != 0


def test_empty_or_whitespace_text_is_rejected():
    with patch("pltr.commands.search.SearchService") as service:
        empty = runner.invoke(app, ["search", ""])
        whitespace = runner.invoke(app, ["search", "   "])

    assert empty.exit_code == 2
    assert whitespace.exit_code == 2
    service.return_value.search.assert_not_called()


def test_limit_gateway_cap_is_enforced():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = _ok_payload()
        accepted = runner.invoke(app, ["search", "Flight", "--limit", "500"])
        rejected = runner.invoke(app, ["search", "Flight", "--limit", "501"])

    assert accepted.exit_code == 0, accepted.output
    service.return_value.search.assert_called_once_with("Flight", limit=500)
    assert rejected.exit_code == 2
    assert "500" in rejected.output


def test_ci_mode_ok_exits_zero_with_machine_block():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = _ok_payload()
        result = runner.invoke(app, ["search", "Flight", "--output-mode", "ci"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "query": "Flight",
        "reason": None,
        "results": 2,
        "status": "ok",
    }


def test_ci_mode_inconclusive_exits_two():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = {
            "status": "inconclusive",
            "reason": "search-null",
            "results": None,
        }
        result = runner.invoke(app, ["search", "Flight", "--output-mode", "ci"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "inconclusive"
    assert payload["reason"] == "search-null"
    assert payload["results"] is None


def test_ci_mode_token_expired_stays_fatal():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.side_effect = TokenExpiredError("expired")
        result = runner.invoke(app, ["search", "Flight", "--output-mode", "ci"])

    assert result.exit_code == 1
    assert "DEGRADED [token-expired]" in result.output


def test_unwritable_output_path_exits_one_without_traceback(tmp_path):
    unwritable = tmp_path / "missing-dir" / "out.txt"
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = _ok_payload()
        result = runner.invoke(app, ["search", "Flight", "--output", str(unwritable)])

    assert result.exit_code == 1
    assert "Error writing output file" in result.output
    assert "Traceback" not in result.output


def _filtered_payload() -> dict:
    return {
        "mode": "filtered-resources",
        "status": "ok",
        "reason": None,
        "server_filter": {"pathStartsWith": ["/Team"]},
        "page_size": 2,
        "page_token": None,
        "next_page_token": "next-1",
        "coverage": "partial",
        "truncated": True,
        "server_page_count": 3,
        "coverage_note": (
            "pathStartsWith is the only verified server-side filter; text and "
            "resource type constraints are applied locally to this returned page"
        ),
        "results": [
            {
                "rid": "ri.notepad.1",
                "name": "Flight Notes",
                "path": "/Team/Flight Notes",
                "type": "Notepad",
                "highlights": [
                    {"field": "path", "matches": ["/Team/Flight Notes & Plans"]}
                ],
            },
            {
                "rid": "ri.dataset.1",
                "name": "Flights",
                "path": "/Team/Flights",
                "type": "Dataset",
                "highlights": [],
            },
            {
                "rid": "ri.notepad.2",
                "name": "Other Notes",
                "path": "/Team/Other",
                "type": "Notepad",
                "highlights": [],
            },
        ],
    }


def test_path_prefix_activates_filtered_mode_with_local_text_and_type_filters():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = _filtered_payload()
        result = runner.invoke(
            app,
            [
                "search",
                "Flight",
                "--path-prefix",
                "/Team",
                "--page-size",
                "2",
                "--resource-type",
                "notepad",
                "--output-mode",
                "agent",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "FILTERED RESOURCE SEARCH [ok]" in result.output
    assert "Flight Notes" in result.output
    assert "ri.dataset.1" not in result.output
    assert "Other Notes" not in result.output
    assert "Coverage: partial" in result.output
    assert "Next page token: next-1" in result.output
    assert "returned page" in result.output
    service.return_value.search_resources.assert_called_once_with(
        ["/Team"], page_size=2, page_token=None
    )
    service.return_value.search.assert_not_called()


def test_filtered_json_preserves_pagination_and_sanitized_highlights():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = _filtered_payload()
        result = runner.invoke(
            app,
            [
                "search",
                "Plans",
                "--path-prefix",
                "/Team",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["coverage"] == "partial"
    assert payload["next_page_token"] == "next-1"
    assert payload["local_filters"]["scope"] == "returned-page-only"
    assert payload["results"][0]["highlights"][0]["matches"] == [
        "/Team/Flight Notes & Plans"
    ]
    assert "<b>" not in result.output
    assert "&amp;" not in result.output


def test_filtered_mode_passes_optional_page_token_and_repeatable_prefixes():
    payload = _filtered_payload()
    payload["next_page_token"] = None
    payload["coverage"] = "complete"
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = payload
        result = runner.invoke(
            app,
            [
                "search",
                "Flight",
                "--path-prefix",
                "/Team",
                "--path-prefix",
                "/Shared",
                "--page-token",
                "current",
            ],
        )

    assert result.exit_code == 0, result.output
    service.return_value.search_resources.assert_called_once_with(
        ["/Team", "/Shared"], page_size=100, page_token="current"
    )


def test_filtered_ci_and_inconclusive_json_preserve_classification():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = _filtered_payload()
        ci_result = runner.invoke(
            app,
            [
                "search",
                "Flight",
                "--path-prefix",
                "/Team",
                "--output-mode",
                "ci",
            ],
        )
        service.return_value.search_resources.return_value = {
            "mode": "filtered-resources",
            "status": "inconclusive",
            "reason": "partial field failure",
            "results": None,
        }
        error_result = runner.invoke(
            app,
            [
                "search",
                "Flight",
                "--path-prefix",
                "/Team",
                "--format",
                "json",
            ],
        )

    assert ci_result.exit_code == 0
    ci_payload = json.loads(ci_result.output)
    assert ci_payload["coverage"] == "partial"
    assert ci_payload["next_page_token"] == "next-1"
    assert error_result.exit_code == 1
    assert json.loads(error_result.output)["status"] == "inconclusive"


def test_page_token_and_resource_type_require_filtered_mode():
    with patch("pltr.commands.search.SearchService") as service:
        token_result = runner.invoke(app, ["search", "Flight", "--page-token", "next"])
        type_result = runner.invoke(
            app, ["search", "Flight", "--resource-type", "Notepad"]
        )

    assert token_result.exit_code == 2
    assert type_result.exit_code == 2
    service.return_value.search.assert_not_called()
    service.return_value.search_resources.assert_not_called()


def test_search_command_remains_registered_with_legacy_invocation():
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = _filtered_payload()
        result = runner.invoke(
            app,
            ["search", "Flight", "--path-prefix", "/Legacy", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["mode"] == "filtered-resources"
    service.return_value.search_resources.assert_called_once_with(
        ["/Legacy"], page_size=100, page_token=None
    )
    service.return_value.search.assert_not_called()


def test_cross_mode_limit_and_page_size_are_rejected():
    with patch("pltr.commands.search.SearchService") as service:
        filtered_limit = runner.invoke(
            app,
            ["search", "Flight", "--path-prefix", "/Team", "--limit", "5"],
        )
        legacy_page_size = runner.invoke(
            app,
            ["search", "Flight", "--page-size", "5"],
        )

    assert filtered_limit.exit_code == 2
    assert "only valid for legacy" in filtered_limit.output
    assert legacy_page_size.exit_code == 2
    assert "requires filtered mode" in legacy_page_size.output
    service.return_value.search.assert_not_called()
    service.return_value.search_resources.assert_not_called()


@pytest.mark.parametrize("page_size", [0, 501])
def test_filtered_page_size_bounds_are_rejected(page_size):
    with patch("pltr.commands.search.SearchService") as service:
        result = runner.invoke(
            app,
            [
                "search",
                "Flight",
                "--path-prefix",
                "/Team",
                "--page-size",
                str(page_size),
            ],
        )

    assert result.exit_code == 2
    assert "between 1 and 500" in result.output
    service.return_value.search_resources.assert_not_called()


@pytest.mark.parametrize(
    ("coverage", "next_page_token", "absence_text"),
    [
        ("complete", None, "path-scoped result"),
        ("partial", "next-empty", "not a stack-wide absence claim"),
    ],
)
def test_filtered_empty_pages_render_coverage_and_continuation(
    coverage, next_page_token, absence_text
):
    payload = _filtered_payload()
    payload.update(
        {
            "coverage": coverage,
            "next_page_token": next_page_token,
            "truncated": coverage == "partial",
            "server_page_count": 0,
            "results": [],
        }
    )
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = payload
        result = runner.invoke(
            app,
            ["search", "Flight", "--path-prefix", "/Team"],
        )

    assert result.exit_code == 0, result.output
    assert "No local matches" in result.output
    assert f"Coverage: {coverage}" in result.output
    assert f"Next page token: {next_page_token or 'none'}" in result.output
    assert absence_text in result.output


def test_table_treats_brackets_as_literal_and_strips_terminal_controls():
    payload = _filtered_payload()
    payload["next_page_token"] = "next\x1b]8;;bad\x07-token"
    payload["results"] = [
        {
            "rid": "ri.[broken\x00",
            "name": "[Example] [broken\x1b]0;owned\x07",
            "path": "/Team/[link](target)\x9b",
            "type": "[Notepad]",
            "highlights": [{"field": "path", "matches": ["[literal]"]}],
        }
    ]
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = payload
        result = runner.invoke(
            app,
            ["search", "literal", "--path-prefix", "/Team"],
        )

    assert result.exit_code == 0, result.output
    assert "[Example] [broken]0;owned" in result.output
    assert "[link](target)" in result.output
    assert "[literal]" in result.output
    assert "\x1b" not in result.output
    assert "\x07" not in result.output
    assert "\x9b" not in result.output


def test_csv_neutralizes_formulas_after_optional_leading_whitespace():
    payload = _ok_payload()
    payload["query"] = "  =query"
    payload["results"] = [
        {
            "rid": "+rid",
            "name": "  =SUM(A1:A2)",
            "path": "\t-cmd",
            "type": "@type",
            "typename": "ResourceMetadata",
        }
    ]
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search.return_value = payload
        result = runner.invoke(
            app,
            ["search", "formula", "--format", "csv"],
        )

    assert result.exit_code == 0, result.output
    row = next(csv.DictReader(StringIO(result.output)))
    assert row["query"] == "'  =query"
    assert row["rid"] == "'+rid"
    assert row["name"] == "'  =SUM(A1:A2)"
    assert row["path"] == "'-cmd"
    assert row["type"] == "'@type"


def test_filtered_csv_exposes_headers_coverage_token_and_empty_metadata_row():
    payload = _filtered_payload()
    payload["results"] = []
    payload["server_page_count"] = 0
    with patch("pltr.commands.search.SearchService") as service:
        service.return_value.search_resources.return_value = payload
        result = runner.invoke(
            app,
            [
                "search",
                "Flight",
                "--path-prefix",
                "/Team",
                "--format",
                "csv",
            ],
        )

    assert result.exit_code == 0, result.output
    reader = csv.DictReader(StringIO(result.output))
    assert reader.fieldnames == [
        "status",
        "query",
        "page_size",
        "page_token",
        "next_page_token",
        "coverage",
        "rid",
        "name",
        "type",
        "path",
        "highlights",
        "reason",
        "warning",
    ]
    row = next(reader)
    assert row["coverage"] == "partial"
    assert row["next_page_token"] == "next-1"
    assert row["rid"] == ""
