"""Main CLI entry point for Portals."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click

from portals import __version__
from portals.adapters.local import LocalFileAdapter
from portals.adapters.notion.adapter import NotionAdapter
from portals.core.conflict_resolver import ConflictResolver, ResolutionStrategy
from portals.core.diff_generator import DiffGenerator
from portals.core.metadata_store import MetadataStore
from portals.core.models import SyncPair
from portals.core.sync_engine import SyncEngine
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
      portals init notion-mirror --teamspace=portals

      # Pair a file with Google Docs
      portals pair memo.md --account=personal

      # Start watching for changes
      portals watch

      # Check sync status
      portals status

      # Sync a specific file
      portals sync path/to/file.md

    For more help on a specific command, run:
      portals COMMAND --help
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
@click.argument("path")
@click.option(
    "--base-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Base directory (defaults to current directory)",
)
@click.pass_context
def resolve(ctx: click.Context, path: str, base_dir: str) -> None:
    """Interactively resolve conflicts for a file.

    Shows differences between local and remote versions and prompts
    for resolution strategy.

    \b
    Example:
      docsync resolve docs/README.md
    """
    logger.info("resolve_command", path=path)

    async def run_resolve() -> None:
        base_path = Path(base_dir).resolve()
        notion_token = os.getenv("NOTION_API_TOKEN")

        if not notion_token:
            click.echo("❌ Error: Notion API token required")
            click.echo("   Set NOTION_API_TOKEN environment variable")
            raise click.Abort()

        # Load metadata
        metadata_store = MetadataStore(base_path=base_path)
        if not metadata_store.exists():
            click.echo("❌ Not initialized. Run 'docsync init' first.")
            raise click.Abort()

        metadata = await metadata_store.load()
        pairs = metadata.get("pairs", [])

        # Find pair for this file
        file_path = Path(path)
        if file_path.is_absolute():
            file_path = file_path.relative_to(base_path)

        pair_data = next(
            (p for p in pairs if Path(p["local_path"]) == file_path),
            None,
        )

        if not pair_data:
            click.echo(f"❌ No sync pair found for {path}")
            raise click.Abort()

        pair = SyncPair.from_dict(pair_data)

        # Initialize adapters and resolver
        local_adapter = LocalFileAdapter(base_path=str(base_path))
        notion_adapter = NotionAdapter(api_token=notion_token)
        sync_engine = SyncEngine(
            local_adapter=local_adapter,
            remote_adapter=notion_adapter,
        )
        resolver = ConflictResolver(
            sync_engine=sync_engine,
            local_adapter=local_adapter,
        )

        try:
            # Read both versions
            local_doc = await local_adapter.read(f"file://{base_path / file_path}")
            remote_doc = await notion_adapter.read(pair.remote_uri)

            # Check if there's actually a conflict
            conflict_info = resolver.get_conflict_info(local_doc, remote_doc)
            if not conflict_info["has_conflict"]:
                click.echo("✅ No conflicts detected - files are identical")
                return

            # Show conflict information
            click.echo(f"\n⚠️  Conflict detected: {path}")
            click.echo("\nBoth local and remote versions have changed since last sync.")
            click.echo("\n" + "━" * 60)

            # Show diff preview
            diff_preview = resolver.format_diff_preview(local_doc, remote_doc, max_lines=15)
            if diff_preview:
                click.echo("\n📊 Changes:")
                click.echo(diff_preview)

            # Show change summary
            changes = conflict_info["changes"]
            click.echo("\n" + "━" * 60)
            click.echo("\n📈 Summary:")
            click.echo(f"   Additions: {changes['additions']} lines")
            click.echo(f"   Deletions: {changes['deletions']} lines")
            click.echo(f"   Changes: {changes['changes']} lines")

            # Prompt for resolution
            click.echo("\n" + "━" * 60)
            click.echo("\nHow would you like to resolve?")
            click.echo("\n[L] Use Local version")
            click.echo("[R] Use Remote (Notion) version")
            click.echo("[M] Merge manually (open editor)")
            click.echo("[D] Show detailed diff")
            click.echo("[C] Cancel")

            while True:
                choice = click.prompt("\nChoice", type=str).upper()

                if choice == "L":
                    strategy = ResolutionStrategy.USE_LOCAL
                    break
                elif choice == "R":
                    strategy = ResolutionStrategy.USE_REMOTE
                    break
                elif choice == "M":
                    strategy = ResolutionStrategy.MERGE_MANUAL
                    break
                elif choice == "D":
                    # Show full diff
                    diff_gen = DiffGenerator()
                    full_diff = diff_gen.generate_unified_diff(
                        local_doc.content,
                        remote_doc.content,
                    )
                    click.echo("\n" + "─" * 60)
                    click.echo(full_diff)
                    click.echo("─" * 60)
                    continue
                elif choice == "C":
                    click.echo("❌ Resolution cancelled")
                    return
                else:
                    click.echo("Invalid choice. Please select L, R, M, D, or C.")
                    continue

            # Apply resolution
            click.echo(f"\n🔄 Applying resolution: {strategy.value}...")
            success = await resolver.resolve_conflict(
                pair,
                local_doc,
                remote_doc,
                strategy,
            )

            if success:
                # Update metadata
                metadata = await metadata_store.load()
                pairs = metadata.get("pairs", [])
                for i, p in enumerate(pairs):
                    if Path(p["local_path"]) == file_path:
                        pairs[i] = pair.to_dict()
                        break
                metadata["pairs"] = pairs
                await metadata_store.save(metadata)

                click.echo(f"✅ Conflict resolved for {path}")
            else:
                click.echo("❌ Resolution failed or cancelled")

        except Exception as e:
            click.echo(f"\n❌ Error resolving conflict: {e}")
            logger.error("resolve_failed", error=str(e))
            raise click.Abort() from e

    asyncio.run(run_resolve())


@cli.command()
@click.option(
    "--auto",
    is_flag=True,
    help="Auto-sync without prompting (default: prompt for each change)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be synced without actually syncing",
)
@click.option(
    "--base-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Base directory (defaults to current directory)",
)
@click.option(
    "--poll-interval",
    type=int,
    default=30,
    help="Seconds between Notion polls (default: 30)",
)
@click.pass_context
def watch(
    ctx: click.Context,
    auto: bool,
    dry_run: bool,
    base_dir: str,
    poll_interval: int,
) -> None:
    """Watch for file changes and sync.

    Runs continuously, monitoring for local and remote changes.
    Press Ctrl+C to stop.

    \\b
    Examples:
      # Watch with prompts (default)
      docsync watch

      # Auto-sync without prompts
      docsync watch --auto

      # Dry run (show what would be synced)
      docsync watch --dry-run

      # Custom poll interval
      docsync watch --poll-interval=60
    """
    # Validate flags
    if auto and dry_run:
        click.echo("❌ Error: Cannot use both --auto and --dry-run")
        raise click.Abort()

    # Determine mode
    if auto:
        mode = "auto"
    elif dry_run:
        mode = "dry_run"
    else:
        mode = "prompt"

    logger.info(
        "watch_command",
        mode=mode,
        base_dir=base_dir,
        poll_interval=poll_interval,
    )

    async def run_watch() -> None:
        base_path = Path(base_dir).resolve()
        notion_token = os.getenv("NOTION_API_TOKEN")

        if not notion_token:
            click.echo("❌ Error: Notion API token required")
            click.echo("   Set NOTION_API_TOKEN environment variable")
            raise click.Abort()

        # Import here to avoid circular import
        from portals.watcher.watch_service import WatchService

        watch_service = WatchService(
            base_path=base_path,
            notion_token=notion_token,
            mode=mode,
            poll_interval=float(poll_interval),
        )

        try:
            # Start watching
            await watch_service.start()

            mode_desc = {
                "auto": "AUTO-SYNC",
                "prompt": "PROMPT",
                "dry_run": "DRY RUN",
            }[mode]

            click.echo(f"\n👀 Watching {base_path}")
            click.echo(f"   Mode: {mode_desc}")
            click.echo(f"   Notion poll interval: {poll_interval}s")
            click.echo(f"   Monitoring {len(watch_service.sync_pairs)} sync pairs")
            click.echo("\n   Press Ctrl+C to stop\n")

            # Keep running until interrupted
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                click.echo("\n\n✋ Stopping watch mode...")

        except Exception as e:
            click.echo(f"\n❌ Watch failed: {e}")
            logger.error("watch_failed", error=str(e))
            raise click.Abort() from e
        finally:
            # Stop watching
            await watch_service.stop()
            click.echo("✅ Watch mode stopped")

    asyncio.run(run_watch())


@cli.command()
@click.pass_context
def version(ctx: click.Context) -> None:
    """Show version information."""
    click.echo(f"Portals version {__version__}")
    click.echo("Multi-platform document synchronization tool")
    click.echo("CLI command: portals")
    click.echo("\nProject: https://github.com/paparomes/portals")


# Register Google Docs commands
from portals.cli.gdocs_commands import pair, list_pairings, unpair, push, pull

cli.add_command(pair)
cli.add_command(push)
cli.add_command(pull)
cli.add_command(unpair)
cli.add_command(list_pairings, name="list-gdocs")


if __name__ == "__main__":
    cli()
