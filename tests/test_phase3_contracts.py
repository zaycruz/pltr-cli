"""Regression guards for the three Phase 3 defects.

Each test fails if its bug comes back:

1. `project update` / `space update` silently erased `description`.
2. Environment-variable authentication was documented but unreachable.
3. `configure configure` prompted with no way for an agent to answer.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from pltr.auth.base import ProfileNotFoundError
from pltr.auth.manager import AuthManager
from pltr.auth.oauth import OAuthClientProvider
from pltr.auth.token import TokenAuthProvider
from pltr.utils.agent_output import AgentPolicyError, configure_agent_settings


class TestReplaceKeepsUnsuppliedFields:
    """`replace()` overwrites everything, so omitted fields must be read back."""

    def _service(self, service_cls, current_description: str):
        """Build a service whose `service` property yields a stub SDK client."""
        client = MagicMock()
        service = service_cls.__new__(service_cls)
        # `service` is a read-only property on the base class, so the stub is
        # installed by patching the property for the duration of the test.
        patcher = patch.object(type(service), "service", property(lambda self: client))
        patcher.start()
        self._patchers.append(patcher)
        current = MagicMock()
        current.display_name = "Original Name"
        current.description = current_description
        return service, client, current

    def setup_method(self):
        self._patchers: list = []

    def teardown_method(self):
        for patcher in self._patchers:
            patcher.stop()

    def test_project_update_preserves_description_on_name_only_change(self):
        from pltr.services.project import ProjectService

        service, client, current = self._service(ProjectService, "keep me")
        client.Project.get.return_value = current

        service.update_project("ri.p.1", display_name="New Name")

        _, kwargs = client.Project.replace.call_args
        assert kwargs["display_name"] == "New Name"
        assert kwargs["description"] == "keep me", (
            "a name-only update must not erase the existing description"
        )

    def test_project_update_preserves_name_on_description_only_change(self):
        from pltr.services.project import ProjectService

        service, client, current = self._service(ProjectService, "old")
        client.Project.get.return_value = current

        service.update_project("ri.p.1", description="new text")

        _, kwargs = client.Project.replace.call_args
        assert kwargs["display_name"] == "Original Name"
        assert kwargs["description"] == "new text"

    def test_space_update_preserves_description_on_name_only_change(self):
        from pltr.services.space import SpaceService

        service, client, current = self._service(SpaceService, "keep me")
        client.Space.get.return_value = current

        service.update_space("ri.s.1", display_name="New Name")

        _, kwargs = client.Space.replace.call_args
        assert kwargs["display_name"] == "New Name"
        assert kwargs["description"] == "keep me"

    def test_explicit_values_are_not_overwritten_by_the_read_back(self):
        from pltr.services.project import ProjectService

        service, client, current = self._service(ProjectService, "old")
        client.Project.get.return_value = current

        service.update_project("ri.p.1", display_name="A", description="B")

        _, kwargs = client.Project.replace.call_args
        assert (kwargs["display_name"], kwargs["description"]) == ("A", "B")
        client.Project.get.assert_not_called()


class TestEnvironmentAuthentication:
    """README and SKILL.md offer env vars for CI; they must actually work."""

    FOUNDRY_VARS = (
        "FOUNDRY_HOST",
        "FOUNDRY_TOKEN",
        "FOUNDRY_CLIENT_ID",
        "FOUNDRY_CLIENT_SECRET",
    )

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in self.FOUNDRY_VARS:
            monkeypatch.delenv(name, raising=False)

    def _manager_without_profiles(self) -> AuthManager:
        manager = AuthManager.__new__(AuthManager)
        manager.storage = MagicMock()
        manager.profile_manager = MagicMock()
        manager.profile_manager.get_active_profile.return_value = None
        return manager

    def test_token_env_vars_authenticate_without_a_profile(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_HOST", "https://stack.example.com")
        monkeypatch.setenv("FOUNDRY_TOKEN", "secret-token")

        provider = AuthManager._provider_from_environment()

        assert isinstance(provider, TokenAuthProvider)
        assert provider.host == "https://stack.example.com"

    def test_oauth_env_vars_authenticate_without_a_profile(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_HOST", "https://stack.example.com")
        monkeypatch.setenv("FOUNDRY_CLIENT_ID", "cid")
        monkeypatch.setenv("FOUNDRY_CLIENT_SECRET", "csecret")

        provider = AuthManager._provider_from_environment()

        assert isinstance(provider, OAuthClientProvider)

    def test_get_client_uses_the_environment_when_no_profile_exists(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_HOST", "https://stack.example.com")
        monkeypatch.setenv("FOUNDRY_TOKEN", "secret-token")
        manager = self._manager_without_profiles()

        with patch.object(
            TokenAuthProvider, "get_client", return_value="client"
        ) as get_client:
            assert manager.get_client() == "client"
        get_client.assert_called_once()

    def test_incomplete_environment_still_reports_the_missing_setup(self):
        monkeypatch_host = os.environ.pop("FOUNDRY_HOST", None)
        assert monkeypatch_host is None
        manager = self._manager_without_profiles()

        with pytest.raises(ProfileNotFoundError) as error:
            manager.get_client()
        # The message must name both routes, or a CI caller cannot self-serve.
        assert "FOUNDRY_TOKEN" in str(error.value)
        assert "configure" in str(error.value)

    def test_a_host_alone_is_not_enough(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_HOST", "https://stack.example.com")
        assert AuthManager._provider_from_environment() is None

    def test_an_explicit_profile_never_falls_back_to_the_environment(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_HOST", "https://stack.example.com")
        monkeypatch.setenv("FOUNDRY_TOKEN", "secret-token")
        manager = self._manager_without_profiles()
        manager.storage.get_profile.side_effect = ProfileNotFoundError("nope")

        with pytest.raises(ProfileNotFoundError):
            manager.get_client(profile="named")
        manager.storage.get_profile.assert_called_once_with("named")


class TestConfigureIsAnswerableByAnAgent:
    """The only true hard block in the CLI: credential setup."""

    def teardown_method(self):
        configure_agent_settings()

    def test_missing_flag_raises_a_policy_error_naming_the_flag(self):
        from pltr.commands.configure import _require_flag

        configure_agent_settings(non_interactive=True)
        with pytest.raises(AgentPolicyError, match="--token"):
            _require_flag("--token")

    def test_interactive_runs_still_prompt(self):
        from pltr.commands.configure import _require_flag

        configure_agent_settings()
        assert _require_flag("--token") is None

    def test_force_flag_exists_on_the_command(self):
        import click
        from typer.main import get_command
        from pltr.cli import app

        root = get_command(app)
        configure_group = root.commands["configure"]
        assert isinstance(configure_group, click.Group)
        command = configure_group.commands["configure"]
        flags = {opt for param in command.params for opt in param.opts}
        assert "--force" in flags, (
            "credential rotation is unreachable for an agent without --force"
        )
