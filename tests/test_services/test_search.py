"""Tests for fail-safe title search via the GraphQL gateway."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from pltr.services.foundry_internal_client import GraphQLResult
from pltr.services.search import (
    FILTER_COVERAGE_NOTE,
    RESOURCE_SEARCH_SORT,
    SEARCH_RESOURCES_QUERY,
    SEARCH_TITLES_QUERY,
    SearchService,
    TRUNCATION_NOTE,
)


def _ok_frame(entries: list[dict]) -> GraphQLResult:
    return GraphQLResult(data={"search": entries})


def test_search_returns_normalized_results_and_truncation_note():
    client = Mock()
    client.graphql.return_value = _ok_frame(
        [
            {
                "__typename": "ResourceMetadata",
                "rid": "ri.foundry.main.dataset.1",
                "name": "Flights",
                "path": "/AIP Now Ontology/Data/Flights",
                "type": {"name": "Dataset", "__typename": "ResourceTypeMetadata"},
            },
            {
                "__typename": "ResourceMetadata",
                "rid": "ri.workshop.main.module.2",
                "name": "Flights Map",
                "path": "/Apps/Flights Map",
                "type": {"name": "Module", "__typename": "ResourceTypeMetadata"},
            },
        ]
    )

    result = SearchService(client=client).search("Flight", limit=4)

    assert result["status"] == "ok"
    assert result["reason"] is None
    assert result["query"] == "Flight"
    assert result["limit"] == 4
    assert result["truncation_note"] == TRUNCATION_NOTE
    assert [r["name"] for r in result["results"]] == ["Flights", "Flights Map"]
    assert result["results"][0]["type"] == "Dataset"
    assert result["results"][1]["rid"] == "ri.workshop.main.module.2"
    client.graphql.assert_called_once_with(
        "SearchTitles", SEARCH_TITLES_QUERY, {"t": "Flight", "l": 4}
    )


def test_empty_result_list_is_ok_not_inconclusive():
    client = Mock()
    client.graphql.return_value = _ok_frame([])

    result = SearchService(client=client).search("nothing matches")

    assert result["status"] == "ok"
    assert result["results"] == []


def test_null_search_field_is_inconclusive_not_empty():
    client = Mock()
    client.graphql.return_value = GraphQLResult(data={"search": None})

    result = SearchService(client=client).search("Flight")

    assert result["status"] == "inconclusive"
    assert result["reason"] == "search-null"
    assert result["results"] is None


def test_graphql_errors_are_inconclusive_with_message():
    client = Mock()
    client.graphql.return_value = GraphQLResult(
        errors=[{"message": "Validation error (FieldUndefined@[search/x]) : ..."}]
    )

    result = SearchService(client=client).search("Flight")

    assert result["status"] == "inconclusive"
    assert "FieldUndefined" in result["reason"]


def test_inconclusive_transport_status_propagates():
    client = Mock()
    client.graphql.return_value = GraphQLResult(
        status="inconclusive", reason="missing-response-frame"
    )

    result = SearchService(client=client).search("Flight")

    assert result["status"] == "inconclusive"
    assert result["reason"] == "missing-response-frame"


def test_malformed_entries_are_skipped_not_fatal():
    client = Mock()
    client.graphql.return_value = _ok_frame(
        [
            "not-a-mapping",
            {
                "__typename": "ResourceMetadata",
                "rid": "ri.compass.main.folder.3",
                "name": "Flight Sensor",
                "path": "/Data/Flight Sensor",
                "type": None,
            },
        ]
    )

    result = SearchService(client=client).search("Flight")

    assert result["status"] == "ok"
    assert len(result["results"]) == 1
    assert result["results"][0]["type"] is None


def test_query_text_matches_verified_shape():
    assert "search(title: $t, limit: $l)" in SEARCH_TITLES_QUERY
    assert "mutation" not in SEARCH_TITLES_QUERY.lower()


def test_hostile_text_travels_only_via_variables():
    # Injection-safe design pin: user text must only reach the gateway as a
    # GraphQL variable, never interpolated into the static query constant.
    client = Mock()
    client.graphql.return_value = _ok_frame([])

    hostile = 'mutation { x } "quoted"\nwith newline'
    SearchService(client=client).search(hostile, limit=7)

    operation, query, variables = client.graphql.call_args.args
    assert operation == "SearchTitles"
    assert query == SEARCH_TITLES_QUERY
    assert variables == {"t": hostile, "l": 7}
    assert hostile not in SEARCH_TITLES_QUERY
    assert "$t" in SEARCH_TITLES_QUERY


def _resource_page(*, next_page_token=None) -> GraphQLResult:
    return GraphQLResult(
        data={
            "searchResources": {
                "nextPageToken": next_page_token,
                "results": [
                    {
                        "highlights": [
                            {
                                "field": "path",
                                "matches": [
                                    "<b>&#x2F;Team</b>&#x2F;Notes &amp; Plans",
                                    "<i>kept</i>",
                                ],
                            }
                        ],
                        "resource": {
                            "rid": "ri.notepad.main.notepad.1",
                            "name": "Flight notes",
                            "path": "/Team/Notes & Plans",
                            "type": {"name": "Notepad"},
                        },
                    }
                ],
            }
        }
    )


def test_search_resources_uses_only_verified_filter_and_normalizes_page():
    client = Mock()
    client.graphql.return_value = _resource_page(next_page_token="next-1")

    result = SearchService(client=client).search_resources(
        ["/Team", "/Shared"],
        page_size=40,
        page_token="current",
    )

    assert result == {
        "mode": "filtered-resources",
        "server_filter": {"pathStartsWith": ["/Team", "/Shared"]},
        "page_size": 40,
        "page_token": "current",
        "coverage_note": FILTER_COVERAGE_NOTE,
        "status": "ok",
        "reason": None,
        "coverage": "partial",
        "truncated": True,
        "next_page_token": "next-1",
        "server_page_count": 1,
        "results": [
            {
                "rid": "ri.notepad.main.notepad.1",
                "name": "Flight notes",
                "path": "/Team/Notes & Plans",
                "type": "Notepad",
                "highlights": [
                    {
                        "field": "path",
                        "matches": ["/Team/Notes & Plans", "<i>kept</i>"],
                    }
                ],
            }
        ],
    }
    client.graphql.assert_called_once_with(
        "SearchResources",
        SEARCH_RESOURCES_QUERY,
        {
            "filter": {"pathStartsWith": ["/Team", "/Shared"]},
            "sort": RESOURCE_SEARCH_SORT,
            "pageSize": 40,
            "pageToken": "current",
        },
    )


def test_search_resources_final_page_has_complete_coverage():
    client = Mock()
    client.graphql.return_value = _resource_page()

    result = SearchService(client=client).search_resources(["/Team"])

    assert result["coverage"] == "complete"
    assert result["truncated"] is False
    assert result["next_page_token"] is None


def test_search_resources_graphql_partial_response_is_inconclusive():
    client = Mock()
    client.graphql.return_value = GraphQLResult(
        data={"searchResources": {"nextPageToken": None, "results": []}},
        errors=[{"message": "partial field failure"}],
    )

    result = SearchService(client=client).search_resources(["/Team"])

    assert result["status"] == "inconclusive"
    assert result["reason"] == "partial field failure"
    assert result["results"] is None
    assert result["server_filter"] == {"pathStartsWith": ["/Team"]}


def test_search_resources_null_page_and_null_results_are_inconclusive():
    client = Mock()
    service = SearchService(client=client)
    client.graphql.return_value = GraphQLResult(data={"searchResources": None})
    null_page = service.search_resources(["/Team"])
    client.graphql.return_value = GraphQLResult(
        data={"searchResources": {"nextPageToken": None, "results": None}}
    )
    null_results = service.search_resources(["/Team"])

    assert null_page["reason"] == "search-resources-null"
    assert null_results["reason"] == "search-resources-results-null"
    assert null_page["results"] is None
    assert null_results["results"] is None


def test_search_resources_query_contains_no_unverified_filter_members():
    assert "$filter: ResourceSearchFilter!" in SEARCH_RESOURCES_QUERY
    assert "$sort: ResourceSearchSort" in SEARCH_RESOURCES_QUERY
    assert "$sort: ResourceSearchSort!" not in SEARCH_RESOURCES_QUERY
    assert "sort: $sort" in SEARCH_RESOURCES_QUERY
    # VERIFIED: the input argument/variable is exactly `pageToken`; the
    # response's continuation field is exactly `nextPageToken`. These are
    # deliberately NOT the same name — pin both directions so a regression
    # to `nextPageToken:` as an input argument fails this contract test.
    assert "$pageToken: String" in SEARCH_RESOURCES_QUERY
    assert "pageToken: $pageToken" in SEARCH_RESOURCES_QUERY
    assert "nextPageToken: $nextPageToken" not in SEARCH_RESOURCES_QUERY
    assert "$nextPageToken: String" not in SEARCH_RESOURCES_QUERY
    # nextPageToken must appear exactly once: as the bare response field,
    # never as an input-argument mapping.
    assert SEARCH_RESOURCES_QUERY.count("nextPageToken") == 1
    assert "highlights { field matches }" in SEARCH_RESOURCES_QUERY
    assert "title" not in SEARCH_RESOURCES_QUERY
    assert "resourceType" not in SEARCH_RESOURCES_QUERY
    assert "mutation" not in SEARCH_RESOURCES_QUERY.lower()


@pytest.mark.parametrize(
    ("next_page_token", "coverage", "truncated"),
    [(None, "complete", False), ("next-empty", "partial", True)],
)
def test_search_resources_empty_pages_preserve_coverage(
    next_page_token, coverage, truncated
):
    client = Mock()
    client.graphql.return_value = GraphQLResult(
        data={
            "searchResources": {
                "nextPageToken": next_page_token,
                "results": [],
            }
        }
    )

    result = SearchService(client=client).search_resources(["/Team"])

    assert result["status"] == "ok"
    assert result["results"] == []
    assert result["server_page_count"] == 0
    assert result["coverage"] == coverage
    assert result["truncated"] is truncated
    assert result["next_page_token"] == next_page_token


@pytest.mark.parametrize("page_size", [0, 501])
def test_search_resources_rejects_out_of_bounds_page_size(page_size):
    client = Mock()

    with pytest.raises(ValueError, match="between 1 and 500"):
        SearchService(client=client).search_resources(
            ["/Team"],
            page_size=page_size,
        )

    client.graphql.assert_not_called()


def test_search_resources_sanitizes_scalars_controls_and_highlight_fidelity():
    client = Mock()
    client.graphql.return_value = GraphQLResult(
        data={
            "searchResources": {
                "nextPageToken": "next\x1b]8;;bad\x07-token\x9b",
                "results": [
                    {
                        "highlights": [
                            {
                                "field": "pa\x00th",
                                "matches": [
                                    "<b>hit</b> &lt;b&gt;literal&lt;/b&gt;\x1b",
                                ],
                            }
                        ],
                        "resource": {
                            "rid": "ri.\x00notepad",
                            "name": "[Example]\x1b]0;owned\x07",
                            "path": "/Team\x9b/path",
                            "type": {"name": "Note\npad"},
                        },
                    }
                ],
            }
        }
    )

    result = SearchService(client=client).search_resources(["/Team"])

    assert result["next_page_token"] == "next]8;;bad-token"
    resource = result["results"][0]
    assert resource["rid"] == "ri.notepad"
    assert resource["name"] == "[Example]]0;owned"
    assert resource["path"] == "/Team/path"
    assert resource["type"] == "Notepad"
    assert resource["highlights"] == [
        {"field": "path", "matches": ["hit <b>literal</b>"]}
    ]


def test_legacy_search_sanitizes_all_returned_resource_scalars():
    client = Mock()
    client.graphql.return_value = _ok_frame(
        [
            {
                "__typename": "Resource\x1bMetadata",
                "rid": "ri.\x00dataset",
                "name": "=Flights\x07",
                "path": "/Data\x9b/Flights",
                "type": {"name": "Data\nset"},
            }
        ]
    )

    result = SearchService(client=client).search("Flight")

    assert result["results"] == [
        {
            "rid": "ri.dataset",
            "name": "=Flights",
            "path": "/Data/Flights",
            "type": "Dataset",
            "typename": "ResourceMetadata",
        }
    ]
