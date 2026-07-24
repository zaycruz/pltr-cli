"""Tests for configure command output and mutation policy contracts."""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from pltr.auth.base import ProfileNotFoundError
from pltr.cli import app


runner = CliRunner()


def profile_manager(
    profiles: list[str],
    *,
    default: str | None = None,
) -> MagicMock:
    manager = MagicMock()
    manager.list_profiles.return_value = profiles
    manager.get_default.return_value = default
    return manager


def test_agent_list_returns_profiles_in_single_envelope() -> None:
    manager = profile_manager(["default", "oauth"], default="default")
    storage = MagicMock()
    storage.get_profile.side_effect = [
        {
            "host": "https://example.palantirfoundry.com",
            "auth_type": "token",
            "token": "do-not-expose",
        },
        {
            "host": "https://oauth.palantirfoundry.com",
            "auth_type": "oauth",
            "client_id": "do-not-expose",
            "client_secret": "do-not-expose",
        },
    ]

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage", return_value=storage),
    ):
        result = runner.invoke(app, ["--agent", "configure", "list"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "pltr-agent-v1"
    assert payload["data"] == {
        "profiles": [
            {
                "name": "default",
                "is_default": True,
                "host": "https://example.palantirfoundry.com",
                "auth_type": "token",
            },
            {
                "name": "oauth",
                "is_default": False,
                "host": "https://oauth.palantirfoundry.com",
                "auth_type": "oauth",
            },
        ]
    }
    assert payload["meta"] == {"result_type": "profile_list"}
    assert "do-not-expose" not in result.stdout


def test_agent_list_returns_empty_profiles_in_single_envelope() -> None:
    manager = profile_manager([])

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage"),
    ):
        result = runner.invoke(app, ["--agent", "configure", "list"])

    assert result.exit_code == 0
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "pltr-agent-v1"
    assert payload["data"] == {"profiles": []}
    assert "No profiles configured" not in result.stdout


def test_agent_list_missing_credentials_uses_null_host_and_warning() -> None:
    manager = profile_manager(["missing"], default="missing")
    storage = MagicMock()
    storage.get_profile.side_effect = ProfileNotFoundError("secret details")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage", return_value=storage),
    ):
        result = runner.invoke(app, ["--agent", "configure", "list"])

    assert result.exit_code == 0
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["data"]["profiles"] == [
        {
            "name": "missing",
            "is_default": True,
            "host": None,
            "auth_type": "Unknown",
        }
    ]
    assert payload["warnings"] == ["Credentials unavailable for profile 'missing'."]
    assert "secret details" not in result.stdout


def test_human_list_missing_credentials_preserves_sentinel() -> None:
    manager = profile_manager(["missing"], default="missing")
    storage = MagicMock()
    storage.get_profile.side_effect = ProfileNotFoundError("missing")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage", return_value=storage),
    ):
        result = runner.invoke(app, ["configure", "list"])

    assert result.exit_code == 0
    assert "Error loading credentials" in result.stdout


def test_agent_list_storage_exception_returns_single_safe_error() -> None:
    manager = profile_manager(["default"], default="default")
    storage = MagicMock()
    storage.get_profile.side_effect = RuntimeError("token=do-not-expose")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage", return_value=storage),
    ):
        result = runner.invoke(app, ["--agent", "configure", "list"])

    assert result.exit_code == 1
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["errors"] == [
        {"type": "error", "message": "Could not list configured profiles"}
    ]
    assert "do-not-expose" not in result.stdout


def test_agent_list_metadata_exception_returns_single_safe_error() -> None:
    manager = profile_manager([])
    manager.list_profiles.side_effect = OSError("secret metadata path")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage"),
    ):
        result = runner.invoke(app, ["--agent", "configure", "list"])

    assert result.exit_code == 1
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["message"] == "Could not list configured profiles"
    assert "secret metadata path" not in result.stdout


def test_agent_set_default_returns_single_success_envelope() -> None:
    manager = profile_manager(["existing"])

    with patch("pltr.commands.configure.ProfileManager", return_value=manager):
        result = runner.invoke(app, ["--agent", "configure", "set-default", "existing"])

    assert result.exit_code == 0
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["meta"]["messages"] == [
        {"level": "success", "message": "Profile 'existing' set as default"}
    ]
    assert payload["errors"] == []
    manager.set_default.assert_called_once_with("existing")


def test_agent_use_returns_single_success_envelope() -> None:
    manager = profile_manager(["existing"])

    with patch("pltr.commands.configure.ProfileManager", return_value=manager):
        result = runner.invoke(app, ["--agent", "configure", "use", "existing"])

    assert result.exit_code == 0
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["meta"]["messages"] == [
        {"level": "success", "message": "Profile 'existing' set as default"}
    ]
    assert payload["errors"] == []
    manager.set_default.assert_called_once_with("existing")


@pytest.mark.parametrize(
    ("command", "failure_stage"),
    [
        ("set-default", "construction"),
        ("use", "list_profiles"),
        ("set-default", "set_default"),
        ("use", "set_default"),
    ],
)
def test_agent_set_default_failures_return_single_safe_error(
    command: str,
    failure_stage: str,
) -> None:
    manager = profile_manager(["existing"])
    secret = f"secret-{failure_stage}-details"

    if failure_stage == "construction":
        manager_patch = patch(
            "pltr.commands.configure.ProfileManager",
            side_effect=RuntimeError(secret),
        )
    else:
        getattr(manager, failure_stage).side_effect = RuntimeError(secret)
        manager_patch = patch(
            "pltr.commands.configure.ProfileManager",
            return_value=manager,
        )

    with manager_patch:
        result = runner.invoke(
            app,
            ["--agent", "configure", command, "existing"],
        )

    assert result.exit_code == 1
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["errors"] == [
        {
            "type": "error",
            "message": "Could not set profile 'existing' as default",
        }
    ]
    assert secret not in result.stdout


def test_human_set_default_unexpected_exception_propagates() -> None:
    secret = "secret-human-details"

    with patch(
        "pltr.commands.configure.ProfileManager",
        side_effect=RuntimeError(secret),
    ):
        result = runner.invoke(
            app,
            ["configure", "set-default", "existing"],
        )

    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == secret
    assert result.stdout == ""


def test_agent_delete_without_force_never_prompts_and_returns_error() -> None:
    manager = profile_manager(["default"], default="default")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage") as storage_class,
        patch(
            "pltr.utils.agent_output.typer.confirm",
            side_effect=AssertionError("prompted"),
        ),
    ):
        result = runner.invoke(app, ["--agent", "configure", "delete", "default"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "pltr-agent-v1"
    assert "explicit --force" in payload["errors"][0]["message"]
    storage_class.return_value.delete_profile.assert_not_called()
    manager.remove_profile.assert_not_called()


def test_non_interactive_delete_without_force_never_prompts_or_mutates() -> None:
    manager = profile_manager(["default"], default="default")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage") as storage_class,
        patch(
            "pltr.utils.agent_output.typer.confirm",
            side_effect=AssertionError("prompted"),
        ),
    ):
        result = runner.invoke(
            app,
            [
                "--agent",
                "--non-interactive",
                "configure",
                "delete",
                "default",
            ],
        )

    assert result.exit_code == 1
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "pltr-agent-v1"
    assert len(payload["errors"]) == 1
    message = " ".join(payload["errors"][0]["message"].split())
    assert "explicit --force" in message
    storage_class.return_value.delete_profile.assert_not_called()
    manager.remove_profile.assert_not_called()


def test_agent_forced_delete_does_not_prompt_and_deletes_profile() -> None:
    manager = profile_manager(["default"], default="default")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage") as storage_class,
        patch(
            "pltr.utils.agent_output.typer.confirm",
            side_effect=AssertionError("prompted"),
        ),
    ):
        result = runner.invoke(
            app,
            ["--agent", "configure", "delete", "default", "--force"],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "pltr-agent-v1"
    assert payload["meta"]["messages"] == [
        {"level": "success", "message": "Profile 'default' deleted"}
    ]
    storage_class.return_value.delete_profile.assert_called_once_with("default")
    manager.remove_profile.assert_called_once_with("default")


def test_agent_delete_missing_profile_returns_single_error_without_mutation() -> None:
    manager = profile_manager([])

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage") as storage_class,
    ):
        result = runner.invoke(
            app, ["--agent", "configure", "delete", "missing", "--force"]
        )

    assert result.exit_code == 1
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["errors"] == [
        {"type": "error", "message": "Profile 'missing' not found"}
    ]
    storage_class.return_value.delete_profile.assert_not_called()
    manager.remove_profile.assert_not_called()


def test_agent_delete_storage_exception_returns_single_safe_error() -> None:
    manager = profile_manager(["default"], default="default")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage") as storage_class,
    ):
        storage_class.return_value.delete_profile.side_effect = RuntimeError(
            "token=do-not-expose"
        )
        result = runner.invoke(
            app, ["--agent", "configure", "delete", "default", "--force"]
        )

    assert result.exit_code == 1
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["message"] == "Could not delete profile 'default'"
    assert "do-not-expose" not in result.stdout
    manager.remove_profile.assert_not_called()


def test_agent_delete_metadata_exception_returns_single_safe_error() -> None:
    manager = profile_manager([])
    manager.list_profiles.side_effect = OSError("secret metadata path")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage") as storage_class,
    ):
        result = runner.invoke(
            app, ["--agent", "configure", "delete", "default", "--force"]
        )

    assert result.exit_code == 1
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["message"] == "Could not delete profile 'default'"
    assert "secret metadata path" not in result.stdout
    storage_class.return_value.delete_profile.assert_not_called()
    manager.remove_profile.assert_not_called()


def test_interactive_delete_cancellation_does_not_mutate() -> None:
    manager = profile_manager(["default"], default="default")

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage") as storage_class,
    ):
        result = runner.invoke(
            app,
            ["configure", "delete", "default"],
            input="n\n",
        )

    assert result.exit_code == 0
    assert "Deletion cancelled." in result.stdout
    storage_class.return_value.delete_profile.assert_not_called()
    manager.remove_profile.assert_not_called()


def test_human_list_preserves_rich_table_output() -> None:
    manager = profile_manager(["default"], default="default")
    storage = MagicMock()
    storage.get_profile.return_value = {
        "host": "https://example.palantirfoundry.com",
        "auth_type": "token",
        "token": "do-not-expose",
    }

    with (
        patch("pltr.commands.configure.ProfileManager", return_value=manager),
        patch("pltr.commands.configure.CredentialStorage", return_value=storage),
    ):
        result = runner.invoke(app, ["configure", "list"])

    assert result.exit_code == 0
    assert "Configured Profiles" in result.stdout
    assert "Profile Name" in result.stdout
    assert "default" in result.stdout
    assert "Host" in result.stdout
    assert "Auth Type" in result.stdout
    assert "token" in result.stdout
    assert "do-not-expose" not in result.stdout
