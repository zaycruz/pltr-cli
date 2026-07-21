"""GET-first HTTP client for Foundry internal dependency-provider reads."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

import requests

from ..auth.storage import CredentialStorage


class TokenExpiredError(RuntimeError):
    """A loud authentication failure that internal providers must not degrade."""

    error_class = "token-expired"
    retryable = False

    def __init__(self, raw: str = "") -> None:
        super().__init__(
            "Foundry session token expired; re-authenticate before dependency analysis"
        )
        self.raw = raw


class FoundryInternalClient:
    """Issue inspectable Conjure-style requests with fresh credentials each time."""

    def __init__(self, profile: str) -> None:
        self.profile = profile

    def conjure(
        self,
        verb: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        expected: Optional[int | Iterable[int]] = None,
        request_timeout: float = 30.0,
    ) -> tuple[int, Any, str]:
        """Return status, permissively parsed payload, and raw body.

        Statuses are intentionally not raised: internal response semantics use
        400/422 responses as contract signals. ``expected`` is accepted as
        operation metadata for callers but does not suppress inspection.
        """

        credentials = CredentialStorage().get_profile(self.profile)
        host = str(credentials.get("host", "")).rstrip("/")
        token = credentials.get("token")
        response = requests.request(
            method=verb.upper(),
            url=f"{host}/{path.lstrip('/')}",
            json=dict(json_body) if json_body is not None else None,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=request_timeout,
        )
        raw = response.text
        try:
            parsed: Any = response.json()
        except (requests.JSONDecodeError, ValueError):
            parsed = raw
        if response.status_code == 401:
            raise TokenExpiredError(raw)
        return response.status_code, parsed, raw
