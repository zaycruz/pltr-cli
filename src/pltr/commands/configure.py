"""
Configuration commands for pltr CLI.
"""

import typer
from typing import Optional
from rich.console import Console
from rich.prompt import Prompt

from ..auth.storage import CredentialStorage
from ..auth.base import ProfileNotFoundError
from ..config.profiles import ProfileManager
from ..utils.agent_output import (
    AgentPolicyError,
    agent_mode_enabled,
    buffer_agent_message,
    buffer_agent_payload,
    non_interactive_enabled,
    require_confirmation,
)

app = typer.Typer()
console = Console()


def _require_flag(option_name: str) -> None:
    """Refuse to prompt when the caller forbade prompts.

    `configure` reads credentials through Rich prompts, which block forever on
    a closed stdin. An agent gets a policy error naming the flag to pass.
    """
    if non_interactive_enabled():
        detail = f"Configuring a profile without prompts requires {option_name}."
        if agent_mode_enabled():
            # Mirror require_confirmation: buffer first, so the refusal reaches
            # the caller as an envelope instead of stderr text and empty stdout.
            buffer_agent_message(detail, level="error")
        raise AgentPolicyError(detail)


@app.command()
def configure(
    profile: Optional[str] = typer.Option(
        "default", "--profile", "-p", help="Profile name"
    ),
    auth_type: Optional[str] = typer.Option(
        None, "--auth-type", help="Authentication type (token or oauth)"
    ),
    host: Optional[str] = typer.Option(None, "--host", help="Foundry host URL"),
    token: Optional[str] = typer.Option(
        None, "--token", help="Bearer token (for token auth)"
    ),
    client_id: Optional[str] = typer.Option(
        None, "--client-id", help="OAuth client ID"
    ),
    client_secret: Optional[str] = typer.Option(
        None, "--client-secret", help="OAuth client secret"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing profile without asking"
    ),
):
    """Configure authentication for Palantir Foundry."""
    storage = CredentialStorage()
    profile_manager = ProfileManager()

    # Ensure profile is not None
    if not profile:
        profile = "default"

    # Check if profile already exists
    if storage.profile_exists(profile):
        if not require_confirmation(
            f"Profile '{profile}' already exists. Overwrite?",
            confirmed=force,
            option_name="--force",
        ):
            console.print("[yellow]Configuration cancelled.[/yellow]")
            raise typer.Exit()

    # Interactive mode if no auth type specified
    if not auth_type:
        _require_flag("--auth-type")
        auth_type = Prompt.ask(
            "Authentication type", choices=["token", "oauth"], default="token"
        )

    # Get host URL
    if not host:
        _require_flag("--host")
        host = Prompt.ask(
            "Foundry host URL", default="https://your-stack.palantirfoundry.com"
        )

    if auth_type not in {"token", "oauth"}:
        # Neither credential branch below matches, so without this the profile
        # is saved with no usable credentials and reported as a success.
        message = (
            f"Unsupported authentication type '{auth_type}'. Choose token or oauth."
        )
        if agent_mode_enabled():
            buffer_agent_message(message, level="error")
        else:
            console.print(f"[red]Error:[/red] {message}")
        raise typer.Exit(2)

    credentials = {"auth_type": auth_type, "host": host}

    if auth_type == "token":
        # Token authentication
        if not token:
            _require_flag("--token")
            token = Prompt.ask("Bearer token", password=True)
        credentials["token"] = token

    elif auth_type == "oauth":
        # OAuth authentication
        if not client_id:
            _require_flag("--client-id")
            client_id = Prompt.ask("OAuth client ID")
        if not client_secret:
            _require_flag("--client-secret")
            client_secret = Prompt.ask("OAuth client secret", password=True)

        credentials["client_id"] = client_id
        credentials["client_secret"] = client_secret

    # Save credentials
    storage.save_profile(profile, credentials)
    profile_manager.add_profile(profile)

    # Set as default if it's the first profile
    if len(profile_manager.list_profiles()) == 1:
        profile_manager.set_default(profile)

    if agent_mode_enabled():
        buffer_agent_payload(
            {"profile": profile, "auth_type": auth_type, "host": host},
            meta={"operation": "configure_profile"},
        )
        buffer_agent_message(
            f"Profile '{profile}' configured successfully", level="success"
        )
    else:
        console.print(f"[green]✓[/green] Profile '{profile}' configured successfully")


@app.command(name="list")
def list_profiles():
    """List all configured profiles."""
    agent_mode = agent_mode_enabled()
    warnings = []
    try:
        profile_manager = ProfileManager()
        storage = CredentialStorage()
        profiles = profile_manager.list_profiles()
        default = profile_manager.get_default()

        profile_data = []
        for profile in profiles:
            try:
                creds = storage.get_profile(profile)
                host = creds.get("host", "Unknown")
                auth_type = creds.get("auth_type", "Unknown")
            except ProfileNotFoundError:
                host = None if agent_mode else "Error loading credentials"
                auth_type = "Unknown"
                warnings.append(f"Credentials unavailable for profile '{profile}'.")

            profile_data.append(
                {
                    "name": profile,
                    "is_default": profile == default,
                    "host": host,
                    "auth_type": auth_type,
                }
            )
    except typer.Exit:
        raise
    except Exception:
        if agent_mode:
            buffer_agent_message("Could not list configured profiles", level="error")
            raise typer.Exit(1)
        raise

    if agent_mode:
        buffer_agent_payload(
            {"profiles": profile_data},
            meta={"result_type": "profile_list"},
            warnings=warnings,
        )
        return

    if not profiles:
        console.print("[yellow]No profiles configured.[/yellow]")
        console.print("Run 'pltr configure' to set up your first profile.")
        return

    from rich.table import Table

    table = Table(title="Configured Profiles")
    table.add_column("Default", justify="center")
    table.add_column("Profile Name", style="cyan")
    table.add_column("Host URL", style="magenta")
    table.add_column("Auth Type", style="green")

    for profile_entry in profile_data:
        is_default = "✓" if profile_entry["is_default"] else ""
        table.add_row(
            is_default,
            profile_entry["name"],
            profile_entry["host"],
            profile_entry["auth_type"],
        )

    console.print(table)


@app.command()
def set_default(
    profile: str = typer.Argument(..., help="Profile name to set as default"),
):
    """Set a profile as the default."""
    agent_mode = agent_mode_enabled()
    try:
        profile_manager = ProfileManager()

        if profile not in profile_manager.list_profiles():
            message = f"Profile '{profile}' not found"
            if agent_mode:
                buffer_agent_message(message, level="error")
            else:
                console.print(f"[red]Error:[/red] {message}")
            raise typer.Exit(1)

        profile_manager.set_default(profile)
    except typer.Exit:
        raise
    except Exception:
        if agent_mode:
            buffer_agent_message(
                f"Could not set profile '{profile}' as default",
                level="error",
            )
            raise typer.Exit(1)
        raise

    message = f"Profile '{profile}' set as default"
    if agent_mode:
        buffer_agent_message(message, level="success")
    else:
        console.print(f"[green]✓[/green] {message}")


@app.command(name="use")
def use_profile(
    profile: str = typer.Argument(..., help="Profile name to use as default"),
):
    """Alias for set-default. Switch to another profile."""
    # This just calls the same logic as set_default
    set_default(profile)


@app.command()
def delete(
    profile: str = typer.Argument(..., help="Profile name to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a profile."""
    agent_mode = agent_mode_enabled()
    try:
        storage = CredentialStorage()
        profile_manager = ProfileManager()
        profile_exists = profile in profile_manager.list_profiles()
    except typer.Exit:
        raise
    except Exception:
        if agent_mode:
            buffer_agent_message(f"Could not delete profile '{profile}'", level="error")
            raise typer.Exit(1)
        raise

    if not profile_exists:
        message = f"Profile '{profile}' not found"
        if agent_mode:
            buffer_agent_message(message, level="error")
        else:
            console.print(f"[red]Error:[/red] {message}")
        raise typer.Exit(1)

    try:
        confirmed = require_confirmation(
            f"Delete profile '{profile}'?",
            confirmed=force,
        )
    except AgentPolicyError as exc:
        if not agent_mode:
            console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    if not confirmed:
        console.print("[yellow]Deletion cancelled.[/yellow]")
        raise typer.Exit()

    try:
        storage.delete_profile(profile)
        profile_manager.remove_profile(profile)
        message = f"Profile '{profile}' deleted"
        if agent_mode:
            buffer_agent_message(message, level="success")
        else:
            console.print(f"[green]✓[/green] {message}")
    except ProfileNotFoundError:
        message = f"Could not delete profile '{profile}'"
        if agent_mode:
            buffer_agent_message(message, level="error")
        else:
            console.print(f"[red]Error:[/red] {message}")
        raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception:
        if agent_mode:
            buffer_agent_message(f"Could not delete profile '{profile}'", level="error")
            raise typer.Exit(1)
        raise
