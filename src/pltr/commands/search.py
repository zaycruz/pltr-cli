"""Read-only cross-resource search of a Foundry stack."""

from __future__ import annotations

import csv
import json
import re
from io import StringIO
from pathlib import Path
from typing import Any, Mapping, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..auth.base import MissingCredentialsError, ProfileNotFoundError
from ..services.foundry_internal_client import TokenExpiredError
from ..services.search import (
    SearchService,
    sanitize_server_value,
    strip_control_characters,
)
from ..utils.agent_output import buffer_agent_payload, resolve_output_format
from ..utils.completion import complete_output_format, complete_profile
from .dependency import OutputMode


def search_command(
    text: str = typer.Argument(
        ...,
        help="Title text, or a page-local text filter with --path-prefix",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        "-p",
        help="Profile name",
        autocompletion=complete_profile,
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Legacy title-search limit (defaults to 25; invalid in filtered mode)",
    ),
    path_prefix: Optional[list[str]] = typer.Option(
        None,
        "--path-prefix",
        help="Verified server-side path prefix (repeatable); activates filtered mode",
    ),
    page_size: Optional[int] = typer.Option(
        None,
        "--page-size",
        help="Filtered-mode page size (defaults to 100; invalid in legacy mode)",
    ),
    page_token: Optional[str] = typer.Option(
        None,
        "--page-token",
        help="Filtered-mode continuation token from a previous result",
    ),
    resource_type: Optional[str] = typer.Option(
        None,
        "--resource-type",
        help="Filtered-mode resource type matched locally on the returned page",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format (table, json, csv)",
        autocompletion=complete_output_format,
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file path"
    ),
    output_mode: OutputMode = typer.Option(OutputMode.GRAPH, "--output-mode"),
) -> None:
    """Search by title, or search a verified path-scoped resource page."""

    if not text.strip():
        raise typer.BadParameter("must not be empty", param_hint="TEXT")
    if format not in {"table", "json", "csv"}:
        raise typer.BadParameter("must be table, json, or csv", param_hint="--format")
    prefixes = [prefix for prefix in (path_prefix or []) if prefix.strip()]
    if path_prefix and len(prefixes) != len(path_prefix):
        raise typer.BadParameter("must not be empty", param_hint="--path-prefix")
    if not prefixes and (page_token is not None or resource_type is not None):
        raise typer.BadParameter(
            "requires at least one --path-prefix",
            param_hint="--page-token/--resource-type",
        )
    if prefixes and limit is not None:
        raise typer.BadParameter(
            "is only valid for legacy title search",
            param_hint="--limit",
        )
    if not prefixes and page_size is not None:
        raise typer.BadParameter(
            "requires filtered mode with --path-prefix",
            param_hint="--page-size",
        )
    effective_limit = 25 if limit is None else limit
    effective_page_size = 100 if page_size is None else page_size
    if not prefixes and effective_limit < 1:
        raise typer.BadParameter("must be >= 1", param_hint="--limit")
    if not prefixes and effective_limit > 500:
        raise typer.BadParameter("must be <= 500", param_hint="--limit")
    if prefixes and (effective_page_size < 1 or effective_page_size > 500):
        raise typer.BadParameter("must be between 1 and 500", param_hint="--page-size")
    try:
        service = SearchService(profile=profile)
        if prefixes:
            result = service.search_resources(
                prefixes,
                page_size=effective_page_size,
                page_token=page_token,
            )
            result = apply_local_resource_filters(
                result,
                text=text,
                resource_type=resource_type,
            )
        else:
            result = service.search(text, limit=effective_limit)
    except TokenExpiredError:
        typer.echo(
            "DEGRADED [token-expired]: Foundry session token expired; "
            "re-authenticate before retrying this search"
        )
        raise typer.Exit(1)
    except (ProfileNotFoundError, MissingCredentialsError) as exc:
        typer.echo(f"Authentication Error: {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(1)

    if output_mode == OutputMode.CI:
        # Repo CI contract (see commands/dependency.py): exit 0 = clean,
        # 2 = needs verification, 1 = fatal.
        results = result.get("results")
        ci_result = {
            "status": result.get("status"),
            "query": result.get("query", text),
            "results": len(results) if isinstance(results, list) else None,
            "reason": result.get("reason"),
        }
        if result.get("mode") == "filtered-resources":
            ci_result.update(
                {
                    "coverage": result.get("coverage"),
                    "next_page_token": result.get("next_page_token"),
                }
            )
        typer.echo(json.dumps(ci_result, sort_keys=True))
        raise typer.Exit(0 if result.get("status") == "ok" else 2)

    rendered = render_resource_result(result, format, output_mode)
    if output is not None:
        try:
            output.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Error writing output file '{output}': {exc}")
            raise typer.Exit(1)
    else:
        typer.echo(rendered, nl=False)

    if result.get("status") == "inconclusive":
        raise typer.Exit(1)


def render_resource_result(
    result: Mapping[str, Any], format_type: str, output_mode: OutputMode
) -> str:
    rendered_result = sanitize_server_value(dict(result))
    if rendered_result.get("status") == "inconclusive":
        rendered_result["warning"] = _inconclusive_banner(rendered_result)
    if resolve_output_format(format_type) == "agent":
        # Buffered, not returned: the caller echoes this string, and a second
        # document on stdout is exactly what the agent contract forbids.
        buffer_agent_payload(rendered_result, meta={"result_type": "search"})
        return ""
    if format_type == "json":
        return json.dumps(rendered_result, indent=2, sort_keys=True) + "\n"
    if format_type == "csv":
        return _render_csv(rendered_result)
    return _render_table(
        rendered_result,
        agent_mode=output_mode == OutputMode.AGENT,
    )


def _render_table(result: Mapping[str, Any], *, agent_mode: bool) -> str:
    if result.get("status") == "inconclusive":
        return _inconclusive_banner(result) + "\n"

    results = result.get("results")
    mode = result.get("mode")
    label = (
        "NOTEPAD LIST"
        if mode == "notepad-list"
        else "FILTERED RESOURCE SEARCH"
        if mode == "filtered-resources"
        else "SEARCH"
    )
    if not isinstance(results, list) or not results:
        prefix = f"{label} [ok]\n" if agent_mode else ""
        if mode in {"filtered-resources", "notepad-list"}:
            query = result.get("query")
            filter_description = (
                f" for '{query}'" if isinstance(query, str) and query else ""
            )
            suffix = (
                " in this returned page; this is not a stack-wide absence claim"
                if result.get("coverage") == "partial"
                else " in the path-scoped result"
            )
            return "\n".join(
                [
                    prefix + f"No local matches{filter_description}{suffix}",
                    f"Coverage: {result.get('coverage')} (page size {result.get('page_size')})",
                    f"Next page token: {result.get('next_page_token') or 'none'}",
                    f"Note: {result.get('coverage_note')}",
                    "",
                ]
            )
        return (
            prefix + f"No results for '{result.get('query')}' "
            "(an empty list here means zero matches, not a read failure)\n"
        )

    prefix = f"{label} [ok]\n" if agent_mode else ""
    if mode in {"filtered-resources", "notepad-list"}:
        local_filter_lines = []
        if mode == "notepad-list":
            local_filter_lines.append(
                "Local type filter: Notepad (case-insensitive, returned page only)"
            )
        if result.get("query"):
            local_filter_lines.append(f"Local text filter: {result.get('query')}")
        header = [
            prefix
            + (local_filter_lines[0] if local_filter_lines else "Local filters: none"),
            *local_filter_lines[1:],
            f"Path prefixes: {', '.join(result.get('server_filter', {}).get('pathStartsWith', []))}",
            f"Results: {len(results)} from {result.get('server_page_count')} returned resources",
            f"Coverage: {result.get('coverage')} (page size {result.get('page_size')})",
            f"Next page token: {result.get('next_page_token') or 'none'}",
            f"Note: {result.get('coverage_note')}",
            "",
        ]
    else:
        header = [
            prefix + f"Query: {result.get('query')}",
            f"Results: {len(results)} (limit {result.get('limit')})",
            f"Note: {result.get('truncation_note')}",
            "",
        ]
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=200)
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("name")
    table.add_column("type")
    table.add_column("rid")
    table.add_column("path")
    if mode in {"filtered-resources", "notepad-list"}:
        table.add_column("highlights")
    for entry in results:
        if not isinstance(entry, Mapping):
            continue
        row = [
            str(entry.get("name") or ""),
            str(entry.get("type") or ""),
            str(entry.get("rid") or ""),
            str(entry.get("path") or ""),
        ]
        if mode in {"filtered-resources", "notepad-list"}:
            row.append(highlight_text(entry))
        table.add_row(*(Text(value) for value in row))
    console.print(table)
    return "\n".join(header) + "\n" + output.getvalue()


def _inconclusive_banner(result: Mapping[str, Any]) -> str:
    return (
        f"INCONCLUSIVE: {result.get('reason', 'unknown')} — this is NOT proof "
        "there are no matching resources"
    )


def _render_csv(result: Mapping[str, Any]) -> str:
    output = StringIO()
    filtered_mode = result.get("mode") in {"filtered-resources", "notepad-list"}
    legacy_fields = [
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
    filtered_fields = [
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
    writer = csv.DictWriter(
        output,
        fieldnames=filtered_fields if filtered_mode else legacy_fields,
    )
    writer.writeheader()
    results = result.get("results")
    if isinstance(results, list) and results:
        for entry in results:
            if not isinstance(entry, Mapping):
                continue
            row = {
                "status": result.get("status"),
                "query": result.get("query"),
                "rid": entry.get("rid"),
                "name": entry.get("name"),
                "type": entry.get("type"),
                "path": entry.get("path"),
                "reason": result.get("reason"),
                "warning": result.get("warning"),
            }
            if filtered_mode:
                row.update(
                    {
                        "page_size": result.get("page_size"),
                        "page_token": result.get("page_token"),
                        "next_page_token": result.get("next_page_token"),
                        "coverage": result.get("coverage"),
                        "highlights": highlight_text(entry),
                    }
                )
            else:
                row["limit"] = result.get("limit")
            writer.writerow(csv_safe_row(row))
    elif not isinstance(results, list) or filtered_mode:
        row = {
            "status": result.get("status"),
            "query": result.get("query"),
            "reason": result.get("reason"),
            "warning": result.get("warning"),
        }
        if filtered_mode:
            row.update(
                {
                    "page_size": result.get("page_size"),
                    "page_token": result.get("page_token"),
                    "next_page_token": result.get("next_page_token"),
                    "coverage": result.get("coverage"),
                }
            )
        else:
            row["limit"] = result.get("limit")
        writer.writerow(csv_safe_row(row))
    return output.getvalue()


def apply_local_resource_filters(
    result: Mapping[str, Any],
    *,
    text: str,
    resource_type: Optional[str],
) -> dict[str, Any]:
    """Apply unverified text/type semantics only to the returned server page."""

    filtered = dict(result)
    filtered["query"] = text
    filtered["local_filters"] = {
        "text": text,
        "resource_type": resource_type,
        "scope": "returned-page-only",
    }
    resources = result.get("results")
    if not isinstance(resources, list):
        return filtered
    text_key = text.casefold()
    type_key = resource_type.casefold() if resource_type is not None else None
    local_results: list[Mapping[str, Any]] = []
    for resource in resources:
        if not isinstance(resource, Mapping):
            continue
        if type_key is not None:
            candidate_type = resource.get("type")
            if (
                not isinstance(candidate_type, str)
                or candidate_type.casefold() != type_key
            ):
                continue
        searchable = [
            str(resource.get("name") or ""),
            str(resource.get("path") or ""),
            highlight_text(resource),
        ]
        if text_key not in "\n".join(searchable).casefold():
            continue
        local_results.append(resource)
    filtered["results"] = local_results
    return filtered


def highlight_text(resource: Mapping[str, Any]) -> str:
    highlights = resource.get("highlights")
    if not isinstance(highlights, list):
        return ""
    values: list[str] = []
    for highlight in highlights:
        if not isinstance(highlight, Mapping):
            continue
        matches = highlight.get("matches")
        if isinstance(matches, list):
            values.extend(str(match) for match in matches)
    return " | ".join(values)


def csv_safe_cell(value: Any) -> Any:
    """Strip controls and neutralize spreadsheet formulas in string cells."""

    if not isinstance(value, str):
        return value
    safe_value = strip_control_characters(value)
    if re.match(r"^\s*[=+\-@]", safe_value):
        return "'" + safe_value
    return safe_value


def csv_safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Apply CSV output hardening to every cell in one row."""

    return {key: csv_safe_cell(value) for key, value in row.items()}
