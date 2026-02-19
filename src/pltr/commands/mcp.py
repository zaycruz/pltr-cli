import os
import shutil
import typer
from typing import Optional
from rich.console import Console

from ..auth.storage import CredentialStorage
from ..config.profiles import ProfileManager
from ..utils.completion import complete_profile

app = typer.Typer(help="Manage Palantir MCP server integration")
console = Console(stderr=True)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def serve(
    ctx: typer.Context,
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name", autocompletion=complete_profile
    ),
    command: str = typer.Option("npx", help="Base command to run (default: npx)"),
    package: str = typer.Option("@palantir/mcp", help="MCP package to run"),
):
    """
    Run the Palantir MCP server using credentials from the active pltr profile.

    Any extra arguments are passed directly to the MCP server command.
    Example: pltr mcp serve -- --help
    """
    profile_manager = ProfileManager()
    storage = CredentialStorage()

    active_profile = profile or profile_manager.get_active_profile()
    if not active_profile:
        console.print("[red]Error:[/red] No profile configured.")
        console.print("Run 'pltr configure' to set up your first profile.")
        raise typer.Exit(1)

    try:
        credentials = storage.get_profile(active_profile)
    except Exception as e:
        console.print(
            f"[red]Error:[/red] Failed to load credentials for profile '{active_profile}': {e}"
        )
        raise typer.Exit(1)

    host = credentials.get("host")
    auth_type = credentials.get("auth_type", "token")

    if auth_type != "token":
        console.print(
            f"[yellow]Warning:[/yellow] Profile '{active_profile}' uses {auth_type} auth. "
            "MCP server wrapper works best with 'token' auth. It may fail to connect."
        )

    token = credentials.get("token")

    if not host or not token:
        console.print(
            f"[red]Error:[/red] Profile '{active_profile}' is missing host or token."
        )
        raise typer.Exit(1)

    env = os.environ.copy()
    env["FOUNDRY_URL"] = host
    env["FOUNDRY_TOKEN"] = token

    cmd_args = [command, "-y", package] + ctx.args

    console.print(
        f"[green]Starting MCP server[/green] using profile: [bold]{active_profile}[/bold] ({host})"
    )

    executable = shutil.which(command)
    if not executable:
        console.print(f"[red]Error:[/red] Command '{command}' not found in PATH.")
        raise typer.Exit(1)

    try:
        os.execvpe(executable, cmd_args, env)
    except OSError as e:
        console.print(f"[red]Error:[/red] Failed to start MCP server: {e}")
        raise typer.Exit(1)
