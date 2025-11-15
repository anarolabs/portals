"""Google Docs CLI commands for Portals."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from portals.adapters.gdocs.adapter import GoogleDocsAdapter
from portals.adapters.local import LocalFileAdapter
from portals.core.pairing import PairingManager
from portals.utils.logging import get_logger

logger = get_logger(__name__)


@click.command()
@click.argument("file_path", type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option(
    "--account",
    default="personal",
    help="Google account to use (personal, consultancy, estatemate)",
)
@click.option(
    "--create",
    is_flag=True,
    help="Create new Google Doc (default: prompt to select existing)",
)
@click.pass_context
def pair(ctx: click.Context, file_path: str, account: str, create: bool) -> None:
    """Pair a local markdown file with a Google Doc.

    \b
    Examples:
      # Pair with personal account (creates new doc)
      portals pair memo.md --account=personal --create

      # Pair with consultancy account
      portals pair report.md --account=consultancy --create

      # Pair with estatemate account
      portals pair proposal.md --account=estatemate --create
    """
    async def run_pair() -> None:
        file_path_abs = Path(file_path).resolve()

        click.echo(f"📎 Pairing {file_path_abs.name} with Google Docs")
        click.echo(f"   Account: {account}")

        try:
            # Read local file
            local_adapter = LocalFileAdapter()
            doc = await local_adapter.read(f"file://{file_path_abs}")

            # Create or select Google Doc
            gdocs_adapter = GoogleDocsAdapter(account=account)

            if create:
                click.echo(f"\n📄 Creating new Google Doc...")
                uri = await gdocs_adapter.create("gdocs://new", doc)
                doc_id = uri.replace("gdocs://", "")
                click.echo(f"   ✅ Created: https://docs.google.com/document/d/{doc_id}/edit")
            else:
                # TODO: Implement selection of existing doc
                click.echo("❌ Selecting existing doc not yet implemented. Use --create flag.")
                raise click.Abort()

            # Save pairing
            pairing_mgr = PairingManager()
            pairing = pairing_mgr.add_pairing(
                local_path=str(file_path_abs),
                platform="gdocs",
                remote_id=doc_id,
                account=gdocs_adapter.account
            )

            click.echo(f"\n✅ Pairing created!")
            click.echo(f"   Local: {file_path_abs}")
            click.echo(f"   Google Doc: {doc_id}")
            click.echo(f"   Account: {pairing.account}")
            click.echo(f"\n💡 Use 'portals push {file_path}' to sync changes to Google Docs")
            click.echo(f"   Use 'portals pull {file_path}' to sync changes from Google Docs")

        except Exception as e:
            click.echo(f"\n❌ Pairing failed: {e}")
            logger.error("pair_failed", error=str(e))
            raise click.Abort() from e

    asyncio.run(run_pair())


@click.command()
@click.pass_context
def list_pairings(ctx: click.Context) -> None:
    """List all Google Docs pairings."""
    try:
        pairing_mgr = PairingManager()
        pairings = pairing_mgr.list_pairings(platform="gdocs")

        if not pairings:
            click.echo("📋 No Google Docs pairings found")
            click.echo("\n💡 Use 'portals pair <file> --account=<account> --create' to create a pairing")
            return

        click.echo(f"📋 Google Docs Pairings ({len(pairings)})")
        click.echo()

        for p in pairings:
            local_path = Path(p.local_path)
            click.echo(f"  • {local_path.name}")
            click.echo(f"    Local: {local_path}")
            click.echo(f"    Doc ID: {p.remote_id}")
            click.echo(f"    Account: {p.account}")
            if p.last_sync:
                click.echo(f"    Last sync: {p.last_sync}")
            click.echo()

    except Exception as e:
        click.echo(f"❌ Error: {e}")
        raise click.Abort() from e


@click.command()
@click.argument("file_path", type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.pass_context
def unpair(ctx: click.Context, file_path: str) -> None:
    """Remove Google Docs pairing for a file."""
    file_path_abs = Path(file_path).resolve()

    try:
        pairing_mgr = PairingManager()

        # Check if pairing exists
        pairing = pairing_mgr.get_pairing(str(file_path_abs))
        if not pairing:
            click.echo(f"❌ No pairing found for {file_path_abs.name}")
            raise click.Abort()

        # Confirm removal
        click.echo(f"🗑️  Remove pairing for {file_path_abs.name}?")
        click.echo(f"   Google Doc: {pairing.remote_id}")
        click.echo(f"   Account: {pairing.account}")

        if not click.confirm("\nAre you sure?"):
            click.echo("❌ Cancelled")
            return

        # Remove pairing
        pairing_mgr.remove_pairing(str(file_path_abs))
        click.echo(f"\n✅ Pairing removed for {file_path_abs.name}")
        click.echo("   (Google Doc was not deleted)")

    except Exception as e:
        click.echo(f"❌ Error: {e}")
        raise click.Abort() from e


@click.command()
@click.argument("file_path", type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.pass_context
def push(ctx: click.Context, file_path: str) -> None:
    """Push local changes to Google Docs.

    Reads the local markdown file and updates the paired Google Doc.

    \b
    Example:
      portals push memo.md
    """
    async def run_push() -> None:
        file_path_abs = Path(file_path).resolve()

        try:
            # Get pairing
            pairing_mgr = PairingManager()
            pairing = pairing_mgr.get_pairing(str(file_path_abs))

            if not pairing or pairing.platform != "gdocs":
                click.echo(f"❌ No Google Docs pairing found for {file_path_abs.name}")
                click.echo("   Use 'portals pair' first")
                raise click.Abort()

            click.echo(f"⬆️  Pushing {file_path_abs.name} to Google Docs")
            click.echo(f"   Account: {pairing.account}")

            # Read local file
            local_adapter = LocalFileAdapter()
            doc = await local_adapter.read(f"file://{file_path_abs}")

            # Update Google Doc
            gdocs_adapter = GoogleDocsAdapter(account=pairing.account)
            uri = f"gdocs://{pairing.remote_id}"
            await gdocs_adapter.write(uri, doc)

            # Update sync state
            import hashlib
            local_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            pairing_mgr.update_sync_state(
                str(file_path_abs),
                local_hash=local_hash,
                remote_hash=local_hash
            )

            click.echo(f"\n✅ Pushed to Google Docs!")
            click.echo(f"   🔗 https://docs.google.com/document/d/{pairing.remote_id}/edit")

        except Exception as e:
            click.echo(f"\n❌ Push failed: {e}")
            logger.error("push_failed", error=str(e))
            raise click.Abort() from e

    asyncio.run(run_push())


@click.command()
@click.argument("file_path", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.pass_context
def pull(ctx: click.Context, file_path: str) -> None:
    """Pull changes from Google Docs to local file.

    Reads the paired Google Doc and updates the local markdown file.

    \b
    Example:
      portals pull memo.md
    """
    async def run_pull() -> None:
        file_path_abs = Path(file_path).resolve()

        try:
            # Get pairing
            pairing_mgr = PairingManager()
            pairing = pairing_mgr.get_pairing(str(file_path_abs))

            if not pairing or pairing.platform != "gdocs":
                click.echo(f"❌ No Google Docs pairing found for {file_path_abs.name}")
                click.echo("   Use 'portals pair' first")
                raise click.Abort()

            click.echo(f"⬇️  Pulling {file_path_abs.name} from Google Docs")
            click.echo(f"   Account: {pairing.account}")

            # Read Google Doc
            gdocs_adapter = GoogleDocsAdapter(account=pairing.account)
            uri = f"gdocs://{pairing.remote_id}"
            doc = await gdocs_adapter.read(uri)

            # Write to local file
            local_adapter = LocalFileAdapter()
            await local_adapter.write(f"file://{file_path_abs}", doc)

            # Update sync state
            import hashlib
            local_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            pairing_mgr.update_sync_state(
                str(file_path_abs),
                local_hash=local_hash,
                remote_hash=local_hash
            )

            click.echo(f"\n✅ Pulled from Google Docs!")
            click.echo(f"   📄 Updated: {file_path_abs}")

        except Exception as e:
            click.echo(f"\n❌ Pull failed: {e}")
            logger.error("pull_failed", error=str(e))
            raise click.Abort() from e

    asyncio.run(run_pull())
