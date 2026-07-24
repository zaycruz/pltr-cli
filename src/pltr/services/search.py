"""Read-only cross-resource search via the Foundry GraphQL gateway."""

from __future__ import annotations

import html
from typing import Any, Mapping, Optional

from ..auth.base import ProfileNotFoundError
from ..auth.manager import AuthManager
from .foundry_internal_client import FoundryInternalClient, GraphQLResult


# VERIFIED against a live stack (see the GraphQL gateway reference): `search`
# accepts exactly `title: String!` and `limit: Int!` and returns
# `[SearchTitlesResult!]!`. `ResourceMetadata` is a confirmed member of that
# abstract type; select `rid`/`name`/`path`/`type` as scalars only. Do NOT add
# a resource-type filter argument — none is verified to exist. The query has
# NO page token, so results are capped at `limit` and the gateway does not
# report whether further matches exist (silent-truncation risk).
SEARCH_TITLES_QUERY = """query SearchTitles($t: String!, $l: Int!) {
  search(title: $t, limit: $l) {
    __typename
    ... on ResourceMetadata {
      rid
      name
      path
      type { name __typename }
    }
  }
}"""

# VERIFIED against a live stack: `searchResources` accepts a filter whose only
# confirmed member is `pathStartsWith: [String!]`, plus bounded `pageSize` and
# an optional `pageToken` input argument (the response's continuation field is
# `nextPageToken`), and the fixed sort below. Do not add title/type members to
# the server filter; callers may apply those constraints locally to the
# returned page.
SEARCH_RESOURCES_QUERY = """query SearchResources(
  $filter: ResourceSearchFilter!
  $sort: ResourceSearchSort
  $pageSize: Int!
  $pageToken: String
) {
  searchResources(
    filter: $filter
    sort: $sort
    pageSize: $pageSize
    pageToken: $pageToken
  ) {
    nextPageToken
    results {
      highlights { field matches }
      resource {
        rid
        name
        path
        type { name }
      }
    }
  }
}"""

RESOURCE_SEARCH_SORT = {
    "field": "LAST_MODIFIED",
    "direction": "DESCENDING",
}
_CONTROL_CHARACTER_TRANSLATION = dict.fromkeys([*range(0x00, 0x20), *range(0x7F, 0xA0)])

TRUNCATION_NOTE = (
    "search(title:) has no page token; results are capped at --limit and the "
    "gateway does not report whether more matches exist"
)
FILTER_COVERAGE_NOTE = (
    "pathStartsWith is the only verified server-side filter; text and resource "
    "type constraints are applied locally to this returned page"
)


class SearchService:
    """Search Foundry resources by title without treating absence as empty."""

    def __init__(
        self,
        profile: Optional[str] = None,
        *,
        client: Optional[FoundryInternalClient] = None,
    ) -> None:
        if client is not None:
            self.client = client
            return
        effective_profile = profile or AuthManager().get_current_profile()
        if not effective_profile:
            raise ProfileNotFoundError(
                "No profile specified and no default profile configured. "
                "Run 'pltr configure configure' to set up authentication."
            )
        self.client = FoundryInternalClient(effective_profile)

    def search(self, title: str, limit: int = 25) -> dict[str, Any]:
        """Return a fail-safe classification of one title search."""

        response = self.client.graphql(
            "SearchTitles",
            SEARCH_TITLES_QUERY,
            {"t": title, "l": limit},
        )
        if response.errors:
            return self._inconclusive(self._error_reason(response))
        if response.status == "inconclusive":
            return self._inconclusive(
                sanitize_server_text(response.reason) or "graphql-response-inconclusive"
            )

        data = response.data
        raw_results = data.get("search") if isinstance(data, Mapping) else None
        if not isinstance(raw_results, list):
            return self._inconclusive("search-null")

        results: list[dict[str, Any]] = []
        for entry in raw_results:
            if not isinstance(entry, Mapping):
                continue
            type_info = entry.get("type")
            results.append(
                {
                    "rid": sanitize_server_text(entry.get("rid")),
                    "name": sanitize_server_text(entry.get("name")),
                    "path": sanitize_server_text(entry.get("path")),
                    "type": (
                        sanitize_server_text(type_info.get("name"))
                        if isinstance(type_info, Mapping)
                        else None
                    ),
                    "typename": sanitize_server_text(entry.get("__typename")),
                }
            )
        return {
            "status": "ok",
            "reason": None,
            "query": title,
            "limit": limit,
            "truncation_note": TRUNCATION_NOTE,
            "results": results,
        }

    def search_resources(
        self,
        path_prefixes: list[str],
        *,
        page_size: int = 100,
        page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search one verified, path-scoped page of Compass resources."""

        if not path_prefixes or any(not prefix.strip() for prefix in path_prefixes):
            raise ValueError("path_prefixes must contain non-empty values")
        if page_size < 1 or page_size > 500:
            raise ValueError("page_size must be between 1 and 500")
        server_filter = {"pathStartsWith": list(path_prefixes)}
        response = self.client.graphql(
            "SearchResources",
            SEARCH_RESOURCES_QUERY,
            {
                "filter": server_filter,
                "sort": RESOURCE_SEARCH_SORT,
                "pageSize": page_size,
                "pageToken": page_token,
            },
        )
        base = {
            "mode": "filtered-resources",
            "server_filter": server_filter,
            "page_size": page_size,
            "page_token": page_token,
            "coverage_note": FILTER_COVERAGE_NOTE,
            "coverage": "inconclusive",
            "truncated": None,
            "next_page_token": None,
        }
        if response.errors:
            return {**base, **self._inconclusive(self._error_reason(response))}
        if response.status == "inconclusive":
            return {
                **base,
                **self._inconclusive(
                    sanitize_server_text(response.reason)
                    or "graphql-response-inconclusive"
                ),
            }

        data = response.data
        search_page = data.get("searchResources") if isinstance(data, Mapping) else None
        if not isinstance(search_page, Mapping):
            return {**base, **self._inconclusive("search-resources-null")}
        raw_results = search_page.get("results")
        if not isinstance(raw_results, list):
            return {**base, **self._inconclusive("search-resources-results-null")}

        results: list[dict[str, Any]] = []
        for entry in raw_results:
            if not isinstance(entry, Mapping):
                continue
            resource = entry.get("resource")
            if not isinstance(resource, Mapping):
                continue
            type_info = resource.get("type")
            raw_highlights = entry.get("highlights")
            highlights: list[dict[str, Any]] = []
            if isinstance(raw_highlights, list):
                for highlight in raw_highlights:
                    if not isinstance(highlight, Mapping):
                        continue
                    matches = highlight.get("matches")
                    highlights.append(
                        {
                            "field": sanitize_server_text(highlight.get("field")),
                            "matches": (
                                [
                                    sanitize_highlight(match)
                                    for match in matches
                                    if isinstance(match, str)
                                ]
                                if isinstance(matches, list)
                                else []
                            ),
                        }
                    )
            results.append(
                {
                    "rid": sanitize_server_text(resource.get("rid")),
                    "name": sanitize_server_text(resource.get("name")),
                    "path": sanitize_server_text(resource.get("path")),
                    "type": (
                        sanitize_server_text(type_info.get("name"))
                        if isinstance(type_info, Mapping)
                        else None
                    ),
                    "highlights": highlights,
                }
            )

        next_page_token = sanitize_server_text(search_page.get("nextPageToken"))
        has_more = isinstance(next_page_token, str) and bool(next_page_token)
        return {
            **base,
            "status": "ok",
            "reason": None,
            "coverage": "partial" if has_more else "complete",
            "truncated": has_more,
            "next_page_token": next_page_token if has_more else None,
            "server_page_count": len(results),
            "results": results,
        }

    @staticmethod
    def _inconclusive(reason: str) -> dict[str, Any]:
        return {
            "status": "inconclusive",
            "reason": reason,
            "results": None,
        }

    @staticmethod
    def _error_reason(response: GraphQLResult) -> str:
        messages = [
            strip_control_characters(str(error.get("message")))
            for error in response.errors
            if error.get("message")
        ]
        reason = sanitize_server_text(response.reason)
        return "; ".join(messages) or reason or "graphql-error"


def strip_control_characters(value: str) -> str:
    """Remove C0/C1 controls, including terminal escape introducers."""

    return value.translate(_CONTROL_CHARACTER_TRANSLATION)


def sanitize_server_text(value: Any) -> Optional[str]:
    """Normalize one optional server-controlled scalar for safe output."""

    return strip_control_characters(value) if isinstance(value, str) else None


def sanitize_server_value(value: Any) -> Any:
    """Recursively remove controls from server-controlled response values."""

    if isinstance(value, str):
        return strip_control_characters(value)
    if isinstance(value, Mapping):
        return {
            strip_control_characters(str(key)): sanitize_server_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_server_value(item) for item in value]
    return value


def sanitize_highlight(value: str) -> str:
    """Remove verified markup before decoding so escaped literal tags survive."""

    without_verified_markup = value.replace("<b>", "").replace("</b>", "")
    return strip_control_characters(html.unescape(without_verified_markup))
