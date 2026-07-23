"""
Resource Role service wrapper for Foundry SDK filesystem API.
"""

from typing import Any, Optional, Dict, List

from foundry_sdk.v2.filesystem.models import PrincipalIdOnly, ResourceRoleIdentifier

from .base import BaseService


class ResourceRoleService(BaseService):
    """Service wrapper for Foundry resource role operations using filesystem API."""

    def _get_service(self) -> Any:
        """Get the Foundry filesystem service."""
        return self.client.filesystem

    def grant_role(
        self,
        resource_rid: str,
        principal_id: str,
        principal_type: str,
        role_name: str,
    ) -> Dict[str, Any]:
        """
        Grant a role to a principal on a resource.

        Args:
            resource_rid: Resource Identifier
            principal_id: Principal (user/group) identifier
            principal_type: Principal type ('User' or 'Group')
            role_name: Role name to grant

        Returns:
            Role grant information
        """
        try:
            role_grant = ResourceRoleIdentifier(
                resource_role_principal=PrincipalIdOnly(principal_id=principal_id),
                role_id=role_name,
            )

            self.service.Resource.Role.add(
                resource_rid,
                roles=[role_grant],
            )
            return {
                "resource_rid": resource_rid,
                "principal_id": principal_id,
                "principal_type": principal_type,
                "role_name": role_name,
            }
        except Exception as e:
            raise RuntimeError(
                f"Failed to grant role '{role_name}' to {principal_type} '{principal_id}' on resource {resource_rid}: {e}"
            )

    def revoke_role(
        self,
        resource_rid: str,
        principal_id: str,
        principal_type: str,
        role_name: str,
    ) -> None:
        """
        Revoke a role from a principal on a resource.

        Args:
            resource_rid: Resource Identifier
            principal_id: Principal (user/group) identifier
            principal_type: Principal type ('User' or 'Group')
            role_name: Role name to revoke

        Raises:
            RuntimeError: If revocation fails
        """
        try:
            role_revocation = ResourceRoleIdentifier(
                resource_role_principal=PrincipalIdOnly(principal_id=principal_id),
                role_id=role_name,
            )

            self.service.Resource.Role.remove(
                resource_rid,
                roles=[role_revocation],
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to revoke role '{role_name}' from {principal_type} '{principal_id}' on resource {resource_rid}: {e}"
            )

    def list_resource_roles(
        self,
        resource_rid: str,
        principal_type: Optional[str] = None,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List all roles granted on a resource.

        Args:
            resource_rid: Resource Identifier
            principal_type: Filter by principal type ('User' or 'Group', optional)
            page_size: Number of items per page (optional)
            page_token: Pagination token (optional)

        Returns:
            List of role grant information dictionaries
        """
        try:
            role_grants = []
            list_params: Dict[str, Any] = {}
            if page_size:
                list_params["page_size"] = page_size
            if page_token:
                list_params["page_token"] = page_token

            # The list method returns an iterator
            for role_grant in self.service.Resource.Role.list(
                resource_rid, **list_params
            ):
                formatted = self._format_role_grant(role_grant, resource_rid)
                if (
                    principal_type is None
                    or formatted["principal_type"].casefold()
                    == principal_type.casefold()
                ):
                    role_grants.append(formatted)
            return role_grants
        except Exception as e:
            raise RuntimeError(f"Failed to list roles for resource {resource_rid}: {e}")

    def _format_role_grant(self, role_grant: Any, resource_rid: str) -> Dict[str, Any]:
        """
        Format role grant information for consistent output.

        Args:
            role_grant: Role grant object from Foundry SDK

        Returns:
            Formatted role grant information dictionary
        """
        principal = role_grant.resource_role_principal
        return {
            "resource_rid": resource_rid,
            "principal_id": getattr(principal, "principal_id", None),
            "principal_type": getattr(principal, "principal_type", "Everyone"),
            "role_name": role_grant.role_id,
        }

    def _format_timestamp(self, timestamp: Any) -> Optional[str]:
        """
        Format timestamp for display.

        Args:
            timestamp: Timestamp object from SDK

        Returns:
            Formatted timestamp string or None
        """
        if timestamp is None:
            return None

        # Handle different timestamp formats from the SDK
        if hasattr(timestamp, "time"):
            return str(timestamp.time)
        return str(timestamp)
