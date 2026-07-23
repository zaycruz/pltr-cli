"""Tests for resource role service."""

from unittest.mock import Mock, patch

import pytest
from foundry_sdk.v2.filesystem.models import PrincipalWithId, ResourceRole

from pltr.services.resource_role import ResourceRoleService


RESOURCE_RID = "ri.compass.main.dataset.123"
USER_ID = "12345678-1234-1234-1234-123456789abc"
GROUP_ID = "87654321-4321-4321-4321-cba987654321"


class TestResourceRoleService:
    """Test cases for ResourceRoleService."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock client with the real SDK resource-role hierarchy."""
        client = Mock()
        client.filesystem = Mock()
        client.filesystem.Resource = Mock()
        client.filesystem.Resource.Role = Mock()
        return client

    @pytest.fixture
    def mock_auth_manager(self, mock_client):
        """Create a mock auth manager."""
        with patch("pltr.services.base.AuthManager") as mock_auth_manager_class:
            mock_auth_manager = Mock()
            mock_auth_manager.get_client.return_value = mock_client
            mock_auth_manager_class.return_value = mock_auth_manager
            yield mock_auth_manager

    @pytest.fixture
    def resource_role_service(self, mock_auth_manager):
        """Create a ResourceRoleService instance with mocked dependencies."""
        return ResourceRoleService()

    def test_get_service(self, resource_role_service, mock_client):
        """Test _get_service returns filesystem service."""
        resource_role_service._client = mock_client
        assert resource_role_service._get_service() == mock_client.filesystem

    def test_grant_role(self, resource_role_service, mock_client):
        """Grant maps to Resource.Role.add with the SDK identifier model."""
        resource_role_service._client = mock_client

        result = resource_role_service.grant_role(
            resource_rid=RESOURCE_RID,
            principal_id=USER_ID,
            principal_type="User",
            role_name="viewer",
        )

        mock_client.filesystem.Resource.Role.add.assert_called_once()
        (resource_rid,) = mock_client.filesystem.Resource.Role.add.call_args.args
        (role_identifier,) = mock_client.filesystem.Resource.Role.add.call_args.kwargs[
            "roles"
        ]
        assert resource_rid == RESOURCE_RID
        assert role_identifier.resource_role_principal.principal_id == USER_ID
        assert role_identifier.role_id == "viewer"
        assert result == {
            "resource_rid": RESOURCE_RID,
            "principal_id": USER_ID,
            "principal_type": "User",
            "role_name": "viewer",
        }

    def test_grant_role_failure(self, resource_role_service, mock_client):
        """Test handling role grant failure."""
        mock_client.filesystem.Resource.Role.add.side_effect = Exception("Grant failed")
        resource_role_service._client = mock_client

        with pytest.raises(
            RuntimeError,
            match="Failed to grant role 'viewer' to User",
        ):
            resource_role_service.grant_role(
                resource_rid=RESOURCE_RID,
                principal_id=USER_ID,
                principal_type="User",
                role_name="viewer",
            )

    def test_revoke_role(self, resource_role_service, mock_client):
        """Revoke maps to Resource.Role.remove with the SDK identifier model."""
        resource_role_service._client = mock_client

        resource_role_service.revoke_role(
            resource_rid=RESOURCE_RID,
            principal_id=USER_ID,
            principal_type="User",
            role_name="viewer",
        )

        mock_client.filesystem.Resource.Role.remove.assert_called_once()
        (resource_rid,) = mock_client.filesystem.Resource.Role.remove.call_args.args
        (role_identifier,) = (
            mock_client.filesystem.Resource.Role.remove.call_args.kwargs["roles"]
        )
        assert resource_rid == RESOURCE_RID
        assert role_identifier.resource_role_principal.principal_id == USER_ID
        assert role_identifier.role_id == "viewer"

    def test_revoke_role_failure(self, resource_role_service, mock_client):
        """Test handling role revoke failure."""
        mock_client.filesystem.Resource.Role.remove.side_effect = Exception(
            "Revoke failed"
        )
        resource_role_service._client = mock_client

        with pytest.raises(
            RuntimeError,
            match="Failed to revoke role 'viewer' from User",
        ):
            resource_role_service.revoke_role(
                resource_rid=RESOURCE_RID,
                principal_id=USER_ID,
                principal_type="User",
                role_name="viewer",
            )

    def test_list_resource_roles(self, resource_role_service, mock_client):
        """List formats the nested principal shape returned by SDK 1.95.0."""
        role_grants = [
            ResourceRole(
                resource_role_principal=PrincipalWithId(
                    principal_id=USER_ID, principal_type="USER"
                ),
                role_id="viewer",
            ),
            ResourceRole(
                resource_role_principal=PrincipalWithId(
                    principal_id=GROUP_ID, principal_type="GROUP"
                ),
                role_id="editor",
            ),
        ]
        mock_client.filesystem.Resource.Role.list.return_value = iter(role_grants)
        resource_role_service._client = mock_client

        result = resource_role_service.list_resource_roles(RESOURCE_RID)

        mock_client.filesystem.Resource.Role.list.assert_called_once_with(RESOURCE_RID)
        assert result == [
            {
                "resource_rid": RESOURCE_RID,
                "principal_id": USER_ID,
                "principal_type": "USER",
                "role_name": "viewer",
            },
            {
                "resource_rid": RESOURCE_RID,
                "principal_id": GROUP_ID,
                "principal_type": "GROUP",
                "role_name": "editor",
            },
        ]

    def test_list_resource_roles_with_filters(self, resource_role_service, mock_client):
        """Principal type is filtered locally because SDK list lacks that argument."""
        role_grants = [
            ResourceRole(
                resource_role_principal=PrincipalWithId(
                    principal_id=USER_ID, principal_type="USER"
                ),
                role_id="viewer",
            ),
            ResourceRole(
                resource_role_principal=PrincipalWithId(
                    principal_id=GROUP_ID, principal_type="GROUP"
                ),
                role_id="editor",
            ),
        ]
        mock_client.filesystem.Resource.Role.list.return_value = iter(role_grants)
        resource_role_service._client = mock_client

        result = resource_role_service.list_resource_roles(
            resource_rid=RESOURCE_RID,
            principal_type="User",
            page_size=10,
            page_token="token123",
        )

        mock_client.filesystem.Resource.Role.list.assert_called_once_with(
            RESOURCE_RID,
            page_size=10,
            page_token="token123",
        )
        assert [role["principal_id"] for role in result] == [USER_ID]
