"""Main CLI entry point for Portals."""

from __future__ import annotations

import click

from portals import __version__
from portals.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)


@click.group()
@click.version_option(version=__version__, prog_name="Portals")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default="INFO",
    envvar="LOG_LEVEL",
    help="Set the logging level",
)
@click.option(
    "--log-format",
    type=click.Choice(["human", "json"], case_sensitive=False),
    default="human",
    envvar="LOG_FORMAT",
    help="Set the logging format",
)
@click.pass_context
def cli(ctx: click.Context, log_level: str, log_format: str) -> None:
    """Portals - Multi-platform document synchronization tool.

    Keeps local markdown files in sync with Notion, Google Docs, and Obsidian.

    \b
    Examples:
      # Initialize mirror mode for Notion sync
      docsync init notion-mirror --teamspace=portals

      # Start watching for changes
      docsync watch

      # Check sync status
      docsync status

      # Sync a specific file
      docsync sync path/to/file.md

    For more help on a specific command, run:
      docsync COMMAND --help
    """
    # Store config in context
    ctx.ensure_object(dict)
    ctx.obj["LOG_LEVEL"] = log_level
    ctx.obj["LOG_FORMAT"] = log_format

    # Configure logging
    configure_logging(level=log_level, format=log_format)

    logger.debug("cli_started", version=__version__, log_level=log_level)


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize Portals in the current directory.

    Sets up sync configuration and metadata storage.
    """
    logger.info("init_command", message="Initialize command (not yet implemented)")
    click.echo("✨ Portals initialization (coming in Phase 3)")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show sync status of all paired documents."""
    logger.info("status_command", message="Status command (not yet implemented)")
    click.echo("📊 Status display (coming in Phase 4)")


@cli.command()
@click.argument("path", required=False)
@click.pass_context
def sync(ctx: click.Context, path: str | None) -> None:
    """Sync documents (bidirectional).

    If PATH is provided, sync only that file.
    Otherwise, sync all paired documents.
    """
    logger.info("sync_command", path=path, message="Sync command (not yet implemented)")
    if path:
        click.echo(f"🔄 Syncing {path} (coming in Phase 4)")
    else:
        click.echo("🔄 Syncing all documents (coming in Phase 4)")


@cli.command()
@click.pass_context
def watch(ctx: click.Context) -> None:
    """Watch for file changes and prompt to sync.

    Runs continuously, monitoring for local and remote changes.
    Press Ctrl+C to stop.
    """
    logger.info("watch_command", message="Watch command (not yet implemented)")
    click.echo("👀 Watch mode (coming in Phase 6)")


@cli.command()
@click.pass_context
def version(ctx: click.Context) -> None:
    """Show version information."""
    click.echo(f"Portals version {__version__}")
    click.echo("Multi-platform document synchronization tool")
    click.echo("CLI command: docsync")
    click.echo("\nProject: https://github.com/paparomes/portals")


if __name__ == "__main__":
    cli()
