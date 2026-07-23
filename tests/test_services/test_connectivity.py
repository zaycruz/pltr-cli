"""
Tests for connectivity service wrapper.
"""

import pytest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from pltr.services.connectivity import ConnectivityService


class TestConnectivityService:
    """Test cases for ConnectivityService."""

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def setup_method(self, method, mock_client):
        """Set up test fixtures."""
        self.mock_client = mock_client
        self.service = ConnectivityService(profile="test")

    def test_init_with_profile(self):
        """Test service initialization with profile."""
        service = ConnectivityService(profile="test-profile")
        assert service.profile == "test-profile"

    def test_init_without_profile(self):
        """Test service initialization without profile."""
        service = ConnectivityService()
        assert service.profile is None

    def test_connections_service_with_connectivity_namespace(self):
        """Test connections_service with modern SDK client namespace."""
        service = ConnectivityService(profile="test")
        service._client = SimpleNamespace(connectivity="connectivity-client")

        assert service.connections_service == "connectivity-client"

    def test_connections_service_missing_namespace_raises(self):
        """Test connections_service raises when no supported namespace exists."""
        service = ConnectivityService(profile="test")
        service._client = SimpleNamespace()

        with pytest.raises(RuntimeError, match="Connectivity service is not available"):
            _ = service.connections_service

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_get_service(self, mock_client):
        """Test _get_service returns client."""
        service = ConnectivityService(profile="test")
        result = service._get_service()
        assert result == mock_client

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_connections_service(self, mock_client):
        """Test connections_service property."""
        service = ConnectivityService(profile="test")
        result = service.connections_service
        assert result == mock_client.connections

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_file_imports_service(self, mock_client):
        """Test file_imports_service property."""
        service = ConnectivityService(profile="test")
        result = service.file_imports_service
        assert result == mock_client.connectivity.Connection.FileImport

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_table_imports_service(self, mock_client):
        """Test table_imports_service property."""
        service = ConnectivityService(profile="test")
        result = service.table_imports_service
        assert result == mock_client.connectivity.Connection.TableImport

    def test_list_connections_filesystem_fallback(self):
        """Test connection listing fallback when SDK list() is unavailable."""
        folder_child = Mock()
        folder_child.rid = "ri.compass.main.folder.abc"
        folder_child.type = "folder"

        connection_child = Mock()
        connection_child.rid = "ri.magritte.main.connection.123"
        connection_child.type = "connection"
        connection_child.display_name = "Warehouse Connection"
        connection_child.description = "Test connection"
        connection_child.status = "ACTIVE"
        connection_child.created_time = "2024-01-01T00:00:00Z"
        connection_child.modified_time = "2024-01-02T00:00:00Z"

        folder_client = Mock()
        folder_client.children.side_effect = [
            [folder_child, connection_child],
            [],
        ]

        connection_client = Mock(spec=["get"])
        connectivity = SimpleNamespace(Connection=connection_client)
        filesystem = SimpleNamespace(Folder=folder_client)

        service = ConnectivityService(profile="test")
        service._client = SimpleNamespace(
            connectivity=connectivity, filesystem=filesystem
        )

        result = service.list_connections()

        assert len(result) == 1
        assert result[0]["rid"] == "ri.magritte.main.connection.123"
        assert result[0]["display_name"] == "Warehouse Connection"
        assert result[0]["connection_type"] == "connection"
        folder_client.children.assert_any_call("ri.compass.main.folder.0", preview=True)

    def test_list_connections_filesystem_fallback_respects_env_start_folder(
        self, monkeypatch
    ):
        """Test filesystem fallback starts at env-configured folder RID."""
        monkeypatch.setenv(
            "PLTR_CONNECTIONS_FALLBACK_START_FOLDER_RID",
            "ri.compass.main.folder.custom-start",
        )

        folder_client = Mock()
        folder_client.children.return_value = []

        connection_client = Mock(spec=["get"])
        connectivity = SimpleNamespace(Connection=connection_client)
        filesystem = SimpleNamespace(Folder=folder_client)

        service = ConnectivityService(profile="test")
        service._client = SimpleNamespace(
            connectivity=connectivity, filesystem=filesystem
        )

        result = service.list_connections()

        assert result == []
        folder_client.children.assert_called_once_with(
            "ri.compass.main.folder.custom-start", preview=True
        )

    def test_list_connections_filesystem_fallback_requires_filesystem(self):
        """Test filesystem fallback raises when filesystem namespace is unavailable."""
        connection_client = Mock(spec=["get"])
        connectivity = SimpleNamespace(Connection=connection_client)

        service = ConnectivityService(profile="test")
        service._client = SimpleNamespace(connectivity=connectivity, filesystem=None)

        with pytest.raises(
            RuntimeError,
            match="Connection.list\\(\\) is unavailable and filesystem fallback is not supported",
        ):
            service.list_connections()

    def test_list_connections_filesystem_fallback_raises_on_scan_limit(self):
        """Test filesystem fallback raises when traversal exceeds folder scan cap."""
        folder_child = Mock()
        folder_child.rid = "ri.compass.main.folder.child"
        folder_child.type = "folder"

        folder_client = Mock()
        folder_client.children.return_value = [folder_child]

        connection_client = Mock(spec=["get"])
        connectivity = SimpleNamespace(Connection=connection_client)
        filesystem = SimpleNamespace(Folder=folder_client)

        service = ConnectivityService(profile="test")
        service._client = SimpleNamespace(
            connectivity=connectivity, filesystem=filesystem
        )
        service.MAX_FALLBACK_FOLDERS = 1

        with pytest.raises(
            RuntimeError, match="Connection discovery exceeded folder scan limit"
        ):
            service.list_connections()

    def test_list_connections_filesystem_fallback_raises_on_start_folder_error(self):
        """Test filesystem fallback raises when starting folder cannot be listed."""
        folder_client = Mock()
        folder_client.children.side_effect = Exception("Permission denied")

        connection_client = Mock(spec=["get"])
        connectivity = SimpleNamespace(Connection=connection_client)
        filesystem = SimpleNamespace(Folder=folder_client)

        service = ConnectivityService(profile="test")
        service._client = SimpleNamespace(
            connectivity=connectivity, filesystem=filesystem
        )

        with pytest.raises(RuntimeError, match="Unable to list fallback start folder"):
            service.list_connections()

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_get_connection_success(self, mock_client):
        """Test successful connection retrieval."""
        mock_connection = Mock()
        mock_connection.rid = "ri.conn.main.connection.123"
        mock_connection.display_name = "Test Connection"
        mock_connection.description = "Test Description"
        mock_connection.connection_type = "JDBC"
        mock_connection.status = "ACTIVE"

        mock_client.connections.Connection.get.return_value = mock_connection

        service = ConnectivityService(profile="test")
        result = service.get_connection("ri.conn.main.connection.123")

        assert result["rid"] == "ri.conn.main.connection.123"
        assert result["display_name"] == "Test Connection"
        mock_client.connections.Connection.get.assert_called_once_with(
            "ri.conn.main.connection.123"
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_get_connection_error(self, mock_client):
        """Test connection retrieval error handling."""
        mock_client.connections.Connection.get.side_effect = Exception("Not found")

        service = ConnectivityService(profile="test")
        with pytest.raises(RuntimeError, match="Failed to get connection"):
            service.get_connection("ri.conn.main.connection.123")

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_execute_file_import_success(self, mock_client):
        """Test successful file import execution."""
        mock_client.connectivity.Connection.FileImport.execute.return_value = (
            "ri.foundry.main.build.123"
        )

        service = ConnectivityService(profile="test")
        result = service.execute_file_import(
            "ri.conn.main.connection.123", "ri.import.main.file.123"
        )

        assert result == {"build_rid": "ri.foundry.main.build.123"}
        mock_client.connectivity.Connection.FileImport.execute.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123",
            file_import_rid="ri.import.main.file.123",
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_execute_table_import_success(self, mock_client):
        """Test successful table import execution."""
        mock_client.connectivity.Connection.TableImport.execute.return_value = (
            "ri.foundry.main.build.456"
        )
        service = ConnectivityService(profile="test")
        result = service.execute_table_import(
            "ri.conn.main.connection.123", "ri.import.main.table.123"
        )

        assert result == {"build_rid": "ri.foundry.main.build.456"}
        mock_client.connectivity.Connection.TableImport.execute.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123",
            table_import_rid="ri.import.main.table.123",
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_list_file_imports_success(self, mock_client):
        """Test successful file imports listing."""
        mock_import = Mock()
        mock_import.rid = "ri.import.main.file.123"
        mock_import.display_name = "Test Import"

        mock_client.connectivity.Connection.FileImport.list.return_value = [mock_import]

        service = ConnectivityService(profile="test")
        result = service.list_file_imports("ri.conn.main.connection.123")

        assert len(result) == 1
        assert result[0]["rid"] == "ri.import.main.file.123"
        mock_client.connectivity.Connection.FileImport.list.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123"
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_get_file_import_success(self, mock_client):
        """Test getting a file import with its parent connection RID."""
        mock_import = Mock()
        mock_import.rid = "ri.import.main.file.123"
        mock_client.connectivity.Connection.FileImport.get.return_value = mock_import

        service = ConnectivityService(profile="test")
        result = service.get_file_import(
            "ri.conn.main.connection.123", "ri.import.main.file.123"
        )

        assert result["rid"] == "ri.import.main.file.123"
        mock_client.connectivity.Connection.FileImport.get.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123",
            file_import_rid="ri.import.main.file.123",
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_list_table_imports_success(self, mock_client):
        """Test listing table imports for a connection."""
        mock_import = Mock()
        mock_import.rid = "ri.import.main.table.123"
        mock_client.connectivity.Connection.TableImport.list.return_value = [
            mock_import
        ]

        service = ConnectivityService(profile="test")
        result = service.list_table_imports("ri.conn.main.connection.123")

        assert len(result) == 1
        assert result[0]["rid"] == "ri.import.main.table.123"
        mock_client.connectivity.Connection.TableImport.list.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123"
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_get_table_import_success(self, mock_client):
        """Test getting a table import with its parent connection RID."""
        mock_import = Mock()
        mock_import.rid = "ri.import.main.table.123"
        mock_client.connectivity.Connection.TableImport.get.return_value = mock_import

        service = ConnectivityService(profile="test")
        result = service.get_table_import(
            "ri.conn.main.connection.123", "ri.import.main.table.123"
        )

        assert result["rid"] == "ri.import.main.table.123"
        mock_client.connectivity.Connection.TableImport.get.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123",
            table_import_rid="ri.import.main.table.123",
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_format_connection_info_complete(self, mock_client):
        """Test connection info formatting with complete data."""
        mock_connection = Mock()
        mock_connection.rid = "ri.conn.main.connection.123"
        mock_connection.display_name = "Test Connection"
        mock_connection.description = "Test Description"
        mock_connection.connection_type = "JDBC"
        mock_connection.status = "ACTIVE"
        mock_connection.created_time = "2023-01-01T00:00:00Z"
        mock_connection.modified_time = "2023-01-01T00:00:00Z"

        service = ConnectivityService(profile="test")
        result = service._format_connection_info(mock_connection)

        expected = {
            "rid": "ri.conn.main.connection.123",
            "display_name": "Test Connection",
            "description": "Test Description",
            "connection_type": "JDBC",
            "status": "ACTIVE",
            "created_time": "2023-01-01T00:00:00Z",
            "modified_time": "2023-01-01T00:00:00Z",
        }
        assert result == expected

    @patch("pltr.services.connectivity.ConnectivityService.client")
    @patch("pltr.services.connectivity.getattr")
    def test_format_connection_info_error(self, mock_getattr, mock_client):
        """Test connection info formatting error fallback."""
        mock_connection = Mock()
        # Make getattr raise an exception
        mock_getattr.side_effect = Exception("Getattr failed")

        service = ConnectivityService(profile="test")
        result = service._format_connection_info(mock_connection)

        # Should fallback to raw format when exception occurs
        assert "raw" in result
        assert str(mock_connection) in result["raw"]

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_format_import_info_complete(self, mock_client):
        """Test import info formatting with complete data."""
        mock_import = Mock()
        mock_import.rid = "ri.import.main.file.123"
        mock_import.display_name = "Test Import"
        mock_import.connection_rid = "ri.conn.main.connection.123"
        mock_import.target_dataset_rid = "ri.foundry.main.dataset.456"
        mock_import.status = "CREATED"
        mock_import.import_type = "FILE"
        mock_import.source = "/path/to/file.csv"
        mock_import.created_time = "2023-01-01T00:00:00Z"
        mock_import.modified_time = "2023-01-01T00:00:00Z"

        service = ConnectivityService(profile="test")
        result = service._format_import_info(mock_import)

        expected = {
            "rid": "ri.import.main.file.123",
            "display_name": "Test Import",
            "connection_rid": "ri.conn.main.connection.123",
            "target_dataset_rid": "ri.foundry.main.dataset.456",
            "status": "CREATED",
            "import_type": "FILE",
            "source": "/path/to/file.csv",
            "created_time": "2023-01-01T00:00:00Z",
            "modified_time": "2023-01-01T00:00:00Z",
        }
        assert result == expected

    def test_looks_like_connection_resource_true_by_rid(self):
        """Test RID-based connection detection heuristic."""
        assert ConnectivityService._looks_like_connection_resource(
            "ri.magritte.main.connection.123", "dataset"
        )

    def test_looks_like_connection_resource_true_by_type(self):
        """Test type-based connection detection heuristic."""
        assert ConnectivityService._looks_like_connection_resource(
            "ri.compass.main.dataset.123", "connection"
        )

    def test_looks_like_connection_resource_false_for_folder(self):
        """Test non-connection resources are not misidentified."""
        assert not ConnectivityService._looks_like_connection_resource(
            "ri.compass.main.folder.123", "folder"
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_create_connection_success(self, mock_client):
        """Test successful connection creation."""
        mock_connection = Mock()
        mock_connection.rid = "ri.conn.main.connection.123"
        mock_connection.display_name = "New Connection"
        mock_connection.description = "Description"
        mock_connection.connection_type = "JDBC"
        mock_connection.status = "ACTIVE"
        mock_connection.created_time = "2023-01-01T00:00:00Z"
        mock_connection.modified_time = "2023-01-01T00:00:00Z"

        mock_client.connections.Connection.create.return_value = mock_connection

        service = ConnectivityService(profile="test")
        result = service.create_connection(
            display_name="New Connection",
            parent_folder_rid="ri.folder.main.123",
            configuration={"host": "localhost"},
            worker={"type": "direct"},
        )

        assert result["rid"] == "ri.conn.main.connection.123"
        assert result["display_name"] == "New Connection"
        mock_client.connections.Connection.create.assert_called_once_with(
            configuration={"host": "localhost"},
            display_name="New Connection",
            parent_folder_rid="ri.folder.main.123",
            worker={"type": "direct"},
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_create_connection_error(self, mock_client):
        """Test connection creation error handling."""
        mock_client.connections.Connection.create.side_effect = Exception(
            "Creation failed"
        )

        service = ConnectivityService(profile="test")
        with pytest.raises(RuntimeError, match="Failed to create connection"):
            service.create_connection(
                display_name="New Connection",
                parent_folder_rid="ri.folder.main.123",
                configuration={"host": "localhost"},
                worker={"type": "direct"},
            )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_get_connection_configuration_success(self, mock_client):
        """Test successful connection configuration retrieval."""
        mock_config = {"host": "localhost", "port": 5432}
        mock_client.connections.Connection.get_configuration.return_value = mock_config

        service = ConnectivityService(profile="test")
        result = service.get_connection_configuration("ri.conn.main.connection.123")

        assert result["connection_rid"] == "ri.conn.main.connection.123"
        assert result["configuration"] == mock_config
        mock_client.connections.Connection.get_configuration.assert_called_once_with(
            "ri.conn.main.connection.123"
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_get_connection_configuration_error(self, mock_client):
        """Test connection configuration retrieval error handling."""
        mock_client.connections.Connection.get_configuration.side_effect = Exception(
            "Not found"
        )

        service = ConnectivityService(profile="test")
        with pytest.raises(RuntimeError, match="Failed to get configuration"):
            service.get_connection_configuration("ri.conn.main.connection.123")

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_update_export_settings_success(self, mock_client):
        """Test successful export settings update."""
        mock_client.connections.Connection.update_export_settings.return_value = None

        service = ConnectivityService(profile="test")
        result = service.update_export_settings(
            "ri.conn.main.connection.123",
            {"exportsEnabled": True},
        )

        assert result["connection_rid"] == "ri.conn.main.connection.123"
        assert result["status"] == "export settings updated"
        mock_client.connections.Connection.update_export_settings.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123",
            export_settings={"exportsEnabled": True},
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_update_export_settings_error(self, mock_client):
        """Test export settings update error handling."""
        mock_client.connections.Connection.update_export_settings.side_effect = (
            Exception("Update failed")
        )

        service = ConnectivityService(profile="test")
        with pytest.raises(RuntimeError, match="Failed to update export settings"):
            service.update_export_settings(
                "ri.conn.main.connection.123",
                {"exportsEnabled": True},
            )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_update_secrets_success(self, mock_client):
        """Test successful secrets update."""
        mock_client.connections.Connection.update_secrets.return_value = None

        service = ConnectivityService(profile="test")
        result = service.update_secrets(
            "ri.conn.main.connection.123",
            {"password": "newpass"},
        )

        assert result["connection_rid"] == "ri.conn.main.connection.123"
        assert result["status"] == "secrets updated"
        mock_client.connections.Connection.update_secrets.assert_called_once_with(
            connection_rid="ri.conn.main.connection.123",
            secrets={"password": "newpass"},
        )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_update_secrets_error(self, mock_client):
        """Test secrets update error handling."""
        mock_client.connections.Connection.update_secrets.side_effect = Exception(
            "Update failed"
        )

        service = ConnectivityService(profile="test")
        with pytest.raises(RuntimeError, match="Failed to update secrets"):
            service.update_secrets(
                "ri.conn.main.connection.123",
                {"password": "newpass"},
            )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_upload_custom_jdbc_drivers_success(self, mock_client, tmp_path):
        """Test successful JDBC driver upload."""
        # Create a temporary JAR file
        jar_file = tmp_path / "driver.jar"
        jar_file.write_bytes(b"fake jar content")

        mock_connection = Mock()
        mock_connection.rid = "ri.conn.main.connection.123"
        mock_connection.display_name = "Test Connection"
        mock_connection.description = ""
        mock_connection.connection_type = "JDBC"
        mock_connection.status = "ACTIVE"
        mock_connection.created_time = "2023-01-01T00:00:00Z"
        mock_connection.modified_time = "2023-01-01T00:00:00Z"

        mock_client.connections.Connection.upload_custom_jdbc_drivers.return_value = (
            mock_connection
        )

        service = ConnectivityService(profile="test")
        result = service.upload_custom_jdbc_drivers(
            "ri.conn.main.connection.123",
            str(jar_file),
        )

        assert result["rid"] == "ri.conn.main.connection.123"
        mock_client.connections.Connection.upload_custom_jdbc_drivers.assert_called_once()

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_upload_custom_jdbc_drivers_file_not_found(self, mock_client):
        """Test JDBC driver upload with non-existent file."""
        service = ConnectivityService(profile="test")
        with pytest.raises(FileNotFoundError, match="File not found"):
            service.upload_custom_jdbc_drivers(
                "ri.conn.main.connection.123",
                "/nonexistent/path/driver.jar",
            )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_upload_custom_jdbc_drivers_invalid_extension(self, mock_client, tmp_path):
        """Test JDBC driver upload with non-JAR file."""
        # Create a temporary non-JAR file
        txt_file = tmp_path / "file.txt"
        txt_file.write_text("not a jar file")

        service = ConnectivityService(profile="test")
        with pytest.raises(ValueError, match="File must be a JAR file"):
            service.upload_custom_jdbc_drivers(
                "ri.conn.main.connection.123",
                str(txt_file),
            )

    @patch("pltr.services.connectivity.ConnectivityService.client")
    def test_upload_custom_jdbc_drivers_api_error(self, mock_client, tmp_path):
        """Test JDBC driver upload API error handling."""
        # Create a temporary JAR file
        jar_file = tmp_path / "driver.jar"
        jar_file.write_bytes(b"fake jar content")

        mock_client.connections.Connection.upload_custom_jdbc_drivers.side_effect = (
            Exception("Upload failed")
        )

        service = ConnectivityService(profile="test")
        with pytest.raises(RuntimeError, match="Failed to upload JDBC driver"):
            service.upload_custom_jdbc_drivers(
                "ri.conn.main.connection.123",
                str(jar_file),
            )
