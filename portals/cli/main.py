"""Main CLI entry point for Portals."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click

from portals import __version__
from portals.services.init_service import InitService
from portals.services.sync_service import SyncService
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
@click.option(
    "--root-page-id",
    required=True,
    help="Notion root page ID where synced pages will be created as children",
)
@click.option(
    "--notion-token",
    envvar="NOTION_API_TOKEN",
    help="Notion API token (or set NOTION_API_TOKEN env var)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview what would be created without actually creating pages",
)
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Directory to sync (defaults to current directory)",
)
@click.pass_context
def init(
    ctx: click.Context,
    root_page_id: str,
    notion_token: str | None,
    dry_run: bool,
    path: str,
) -> None:
    """Initialize Portals mirror mode for Notion sync.

    Sets up bidirectional sync between a local directory and Notion pages.

    \b
    Example:
      docsync init --root-page-id=abc123 --notion-token=secret_xxx

    Or set NOTION_API_TOKEN environment variable:
      export NOTION_API_TOKEN=secret_xxx
      docsync init --root-page-id=abc123
    """
    # Get Notion token
    if not notion_token:
        click.echo("❌ Error: Notion API token required")
        click.echo("   Set --notion-token or NOTION_API_TOKEN environment variable")
        raise click.Abort()

    logger.info(
        "init_command",
        root_page_id=root_page_id,
        dry_run=dry_run,
        path=path,
    )

    # Run async initialization
    async def run_init() -> None:
        base_path = Path(path).resolve()

        click.echo(f"🔍 Initializing Portals in {base_path}")
        if dry_run:
            click.echo("   (DRY RUN - no pages will be created)")

        # Create init service
        init_service = InitService(
            base_path=base_path,
            notion_token=notion_token,
            root_page_id=root_page_id,
        )

        # Run initialization
        try:
            result = await init_service.initialize_mirror_mode(dry_run=dry_run)

            if result.success:
                click.echo("\n✅ Initialization complete!")
                click.echo(f"   Files synced: {result.files_synced}")
                click.echo(f"   Pages created: {result.pages_created}")

                if not dry_run:
                    click.echo(f"\n💡 Metadata saved to {base_path / '.docsync'}")
                    click.echo("   Run 'docsync sync' to perform bidirectional sync")
                    click.echo("   Run 'docsync watch' to auto-sync changes")
            else:
                click.echo("\n⚠️  Initialization completed with errors:")
                for error in result.errors:
                    click.echo(f"   - {error}")

        except Exception as e:
            click.echo(f"\n❌ Initialization failed: {e}")
            logger.error("init_failed", error=str(e))
            raise click.Abort() from e

    asyncio.run(run_init())


@cli.command()
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Directory to check status (defaults to current directory)",
)
@click.pass_context
def status(ctx: click.Context, path: str) -> None:
    """Show sync status of all paired documents."""
    logger.info("status_command", path=path)

    async def run_status() -> None:
        base_path = Path(path).resolve()
        notion_token = os.getenv("NOTION_API_TOKEN")

        sync_service = SyncService(
            base_path=base_path,
            notion_token=notion_token,
        )

        try:
            status_info = await sync_service.get_status()

            if not status_info["initialized"]:
                click.echo("❌ Not initialized. Run 'docsync init' first.")
                return

            mode = status_info.get("mode", "unknown")
            pairs_count = status_info["pairs_count"]

            click.echo(f"📊 Sync Status for {base_path}")
            click.echo(f"   Mode: {mode}")
            click.echo(f"   Pairs: {pairs_count}")

            if pairs_count == 0:
                click.echo("\n⚠️  No sync pairs found")
                return

            # Show pairs with conflicts
            conflicts = [p for p in status_info["pairs"] if p["has_conflict"]]
            if conflicts:
                click.echo(f"\n⚠️  {len(conflicts)} pairs with conflicts:")
                for pair in conflicts:
                    click.echo(f"   - {pair['local_path']}")

            # Show recent syncs
            click.echo(f"\n✅ {pairs_count - len(conflicts)} pairs synced")

        except Exception as e:
            click.echo(f"❌ Error getting status: {e}")
            logger.error("status_failed", error=str(e))
            raise click.Abort() from e

    asyncio.run(run_status())


@cli.command()
@click.argument("path", required=False)
@click.option(
    "--force-push",
    is_flag=True,
    help="Force push local changes to remote (ignore conflicts)",
)
@click.option(
    "--force-pull",
    is_flag=True,
    help="Force pull remote changes to local (ignore conflicts)",
)
@click.option(
    "--base-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Base directory (defaults to current directory)",
)
@click.pass_context
def sync(
    ctx: click.Context,
    path: str | None,
    force_push: bool,
    force_pull: bool,
    base_dir: str,
) -> None:
    """Sync documents (bidirectional).

    If PATH is provided, sync only that file.
    Otherwise, sync all paired documents.

    \b
    Examples:
      # Sync all documents
      docsync sync

      # Sync specific file
      docsync sync docs/README.md

      # Force push (override conflicts)
      docsync sync --force-push

      # Force pull (override conflicts)
      docsync sync docs/README.md --force-pull
    """
    # Validate force flags
    if force_push and force_pull:
        click.echo("❌ Error: Cannot use both --force-push and --force-pull")
        raise click.Abort()

    force_direction = None
    if force_push:
        force_direction = "push"
    elif force_pull:
        force_direction = "pull"

    logger.info("sync_command", path=path, force_direction=force_direction)

    async def run_sync() -> None:
        base_path = Path(base_dir).resolve()
        notion_token = os.getenv("NOTION_API_TOKEN")

        if not notion_token:
            click.echo("❌ Error: Notion API token required")
            click.echo("   Set NOTION_API_TOKEN environment variable")
            raise click.Abort()

        sync_service = SyncService(
            base_path=base_path,
            notion_token=notion_token,
        )

        try:
            if path:
                # Sync single file
                click.echo(f"🔄 Syncing {path}...")
                result = await sync_service.sync_file(path, force_direction)

                if result.is_success():
                    click.echo(f"✅ {result.message}")
                elif result.is_conflict():
                    click.echo(f"⚠️  {result.message}")
                    click.echo("   Use --force-push or --force-pull to resolve")
                else:
                    click.echo(f"❌ {result.message}")

            else:
                # Sync all files
                click.echo("🔄 Syncing all documents...")
                summary = await sync_service.sync_all(force_direction)

                click.echo("\n✅ Sync complete:")
                click.echo(f"   Success: {summary.success}")
                click.echo(f"   No changes: {summary.no_changes}")

                if summary.conflicts > 0:
                    click.echo(f"   ⚠️  Conflicts: {summary.conflicts}")
                    click.echo("   Files with conflicts:")
                    for pair in summary.conflict_pairs:
                        click.echo(f"      - {pair.local_path}")
                    click.echo("   Use --force-push or --force-pull to resolve")

                if summary.errors > 0:
                    click.echo(f"   ❌ Errors: {summary.errors}")

        except Exception as e:
            click.echo(f"\n❌ Sync failed: {e}")
            logger.error("sync_failed", error=str(e))
            raise click.Abort() from e

    asyncio.run(run_sync())


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
