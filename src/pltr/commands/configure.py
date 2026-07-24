"""
Configuration commands for pltr CLI.
"""

import typer
from typing import Optional
from rich.console import Console
from rich.prompt import Prompt, Confirm

from ..auth.storage import CredentialStorage
from ..utils.agent_output import (
    AgentPolicyError,
    agent_mode_enabled,
    buffer_agent_payload,
    non_interactive_enabled,
    require_confirmation,
)
from ..auth.base import ProfileNotFoundError
from ..config.profiles import ProfileManager

app = typer.Typer()
console = Console()


def _require_flag(option_name: str) -> None:
    """Refuse to prompt when the caller forbade prompts.

    `configure` reads credentials through Rich prompts, which block forever on
    a closed stdin. An agent gets a policy error naming the flag to pass.
    """
    if non_interactive_enabled():
        raise AgentPolicyError(
            f"Configuring a profile without prompts requires {option_name}."
        )


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

    console.print(f"[green]✓[/green] Profile '{profile}' configured successfully")


@app.command(name="list")
def list_profiles():
    """List all configured profiles."""
    profile_manager = ProfileManager()
    storage = CredentialStorage()
    profiles = profile_manager.list_profiles()
    default = profile_manager.get_default()

    if not profiles:
        if agent_mode_enabled():
            buffer_agent_payload([], meta={"operation": "list_profiles"})
            return
        console.print("[yellow]No profiles configured.[/yellow]")
        console.print("Run 'pltr configure' to set up your first profile.")
        return

    if agent_mode_enabled():
        # An agent needs to know which stack it is pointed at before issuing a
        # destructive call, so host and auth type are part of the contract.
        records = []
        for profile in profiles:
            try:
                creds = storage.get_profile(profile)
                host, auth_type = creds.get("host"), creds.get("auth_type")
            except ProfileNotFoundError:
                host, auth_type = None, None
            records.append(
                {
                    "profile": profile,
                    "default": profile == default,
                    "host": host,
                    "auth_type": auth_type,
                }
            )
        buffer_agent_payload(records, meta={"operation": "list_profiles"})
        return

    from rich.table import Table

    table = Table(title="Configured Profiles")
    table.add_column("Default", justify="center")
    table.add_column("Profile Name", style="cyan")
    table.add_column("Host URL", style="magenta")
    table.add_column("Auth Type", style="green")

    for profile in profiles:
        is_default = "✓" if profile == default else ""
        try:
            creds = storage.get_profile(profile)
            host = creds.get("host", "Unknown")
            auth_type = creds.get("auth_type", "Unknown")
        except ProfileNotFoundError:
            host = "Error loading credentials"
            auth_type = "Unknown"

        table.add_row(is_default, profile, host, auth_type)

    console.print(table)


@app.command()
def set_default(
    profile: str = typer.Argument(..., help="Profile name to set as default"),
):
    """Set a profile as the default."""
    profile_manager = ProfileManager()

    if profile not in profile_manager.list_profiles():
        console.print(f"[red]Error:[/red] Profile '{profile}' not found")
        raise typer.Exit(1)

    profile_manager.set_default(profile)
    console.print(f"[green]✓[/green] Profile '{profile}' set as default")


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
    storage = CredentialStorage()
    profile_manager = ProfileManager()

    if profile not in profile_manager.list_profiles():
        console.print(f"[red]Error:[/red] Profile '{profile}' not found")
        raise typer.Exit(1)

    if not force:
        if not Confirm.ask(f"Delete profile '{profile}'?"):
            console.print("[yellow]Deletion cancelled.[/yellow]")
            raise typer.Exit()

    try:
        storage.delete_profile(profile)
        profile_manager.remove_profile(profile)
        console.print(f"[green]✓[/green] Profile '{profile}' deleted")
    except ProfileNotFoundError:
        console.print(f"[red]Error:[/red] Could not delete profile '{profile}'")
        raise typer.Exit(1)
