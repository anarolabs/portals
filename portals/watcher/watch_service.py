"""Watch service for coordinating file and Notion monitoring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from notion_client import AsyncClient

from portals.adapters.local import LocalFileAdapter
from portals.adapters.notion.adapter import NotionAdapter
from portals.core.metadata_store import MetadataStore
from portals.core.models import SyncPair
from portals.core.sync_engine import SyncEngine
from portals.utils.logging import get_logger
from portals.watcher.file_watcher import ChangeEvent, FileWatcher
from portals.watcher.notion_poller import NotionPoller, RemoteChange

logger = get_logger(__name__)


class WatchMode:
    """Watch mode configuration."""

    AUTO = "auto"  # Auto-sync without prompts
    PROMPT = "prompt"  # Prompt user for each change
    DRY_RUN = "dry_run"  # Show what would be synced


class WatchService:
    """Service for watching local and remote changes."""

    def __init__(
        self,
        base_path: Path,
        notion_token: str,
        mode: str = WatchMode.PROMPT,
        poll_interval: float = 30.0,
        debounce_seconds: float = 2.0,
    ) -> None:
        """Initialize watch service.

        Args:
            base_path: Base directory to watch
            notion_token: Notion API token
            mode: Watch mode (auto, prompt, dry_run)
            poll_interval: Seconds between Notion polls
            debounce_seconds: Seconds to debounce file changes
        """
        self.base_path = base_path
        self.notion_token = notion_token
        self.mode = mode
        self.poll_interval = poll_interval
        self.debounce_seconds = debounce_seconds

        # Initialize components
        self.metadata_store = MetadataStore(base_path=base_path)
        self.local_adapter = LocalFileAdapter()
        self.notion_adapter = NotionAdapter(api_token=notion_token)
        self.sync_engine = SyncEngine(
            local_adapter=self.local_adapter,
            remote_adapter=self.notion_adapter,
        )

        # Watchers (initialized in start())
        self.file_watcher: FileWatcher | None = None
        self.notion_poller: NotionPoller | None = None
        self.sync_pairs: list[SyncPair] = []

        # State
        self.is_running = False
        self.always_sync = False  # Set to True if user chooses "Always"
        self.event_loop: asyncio.AbstractEventLoop | None = None

        logger.info(
            "watch_service_initialized",
            base_path=str(base_path),
            mode=mode,
        )

    async def load_sync_pairs(self) -> None:
        """Load sync pairs from metadata store."""
        if not self.metadata_store.exists():
            raise RuntimeError(
                "Not initialized. Run 'docsync init' first."
            )

        metadata = await self.metadata_store.load()
        pairs_data = metadata.get("pairs", [])

        # Handle both list and dict formats
        if isinstance(pairs_data, dict):
            pairs_data = list(pairs_data.values())

        self.sync_pairs = [SyncPair.from_dict(p) for p in pairs_data]

        logger.info("sync_pairs_loaded", count=len(self.sync_pairs))

    def _handle_local_change(self, change_event: ChangeEvent) -> None:
        """Handle local file change event.

        Args:
            change_event: File change event
        """
        # Schedule async processing in the main event loop
        if self.event_loop and self.event_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._process_local_change(change_event),
                self.event_loop
            )
        else:
            logger.warning(
                "event_loop_not_running",
                message="Cannot process change - event loop not running"
            )

    async def _process_local_change(
        self,
        change_event: ChangeEvent,
    ) -> None:
        """Process local file change.

        Args:
            change_event: File change event
        """
        try:
            logger.info(
                "local_change_detected",
                path=str(change_event.path),
                event_type=change_event.event_type,
            )

            # Find sync pair for this file
            pair = self._find_pair_for_local_path(change_event.path)
            if not pair:
                logger.debug(
                    "no_sync_pair_for_file",
                    path=str(change_event.path),
                )
                return

            # Check if should sync
            should_sync = await self._prompt_for_sync(
                direction="push",
                path=change_event.path,
                event_type=change_event.event_type,
            )

            if not should_sync:
                logger.info("sync_skipped_by_user", path=str(change_event.path))
                return

            # Perform sync
            result = await self.sync_engine.sync_pair(pair)

            if result.is_success():
                logger.info(
                    "local_change_synced",
                    path=str(change_event.path),
                    status=result.status.value,
                )
                # Update metadata
                await self._save_updated_pair(pair)
            else:
                logger.warning(
                    "local_change_sync_failed",
                    path=str(change_event.path),
                    status=result.status.value,
                    message=result.message,
                )

        except Exception as e:
            logger.error(
                "error_processing_local_change",
                path=str(change_event.path),
                error=str(e),
            )

    async def _process_remote_change(
        self,
        remote_change: RemoteChange,
    ) -> None:
        """Process remote Notion change.

        Args:
            remote_change: Remote change event
        """
        try:
            logger.info(
                "remote_change_detected",
                path=str(remote_change.pair.local_path),
                last_edited=remote_change.last_edited_time.isoformat(),
            )

            # Check if should sync
            should_sync = await self._prompt_for_sync(
                direction="pull",
                path=remote_change.pair.local_path,
                event_type="remote_modified",
            )

            if not should_sync:
                logger.info(
                    "sync_skipped_by_user",
                    path=str(remote_change.pair.local_path),
                )
                return

            # Perform sync
            result = await self.sync_engine.sync_pair(remote_change.pair)

            if result.is_success():
                logger.info(
                    "remote_change_synced",
                    path=str(remote_change.pair.local_path),
                    status=result.status.value,
                )
                # Update metadata
                await self._save_updated_pair(remote_change.pair)
            else:
                logger.warning(
                    "remote_change_sync_failed",
                    path=str(remote_change.pair.local_path),
                    status=result.status.value,
                    message=result.message,
                )

        except Exception as e:
            logger.error(
                "error_processing_remote_change",
                path=str(remote_change.pair.local_path),
                error=str(e),
            )

    async def _prompt_for_sync(
        self,
        direction: str,
        path: Path,
        event_type: str,
    ) -> bool:
        """Prompt user whether to sync.

        Args:
            direction: Sync direction (push or pull)
            path: File path
            event_type: Type of change event

        Returns:
            True if should sync
        """
        # Auto mode - always sync
        if self.mode == WatchMode.AUTO:
            return True

        # Dry run mode - never sync
        if self.mode == WatchMode.DRY_RUN:
            logger.info(
                "dry_run_would_sync",
                direction=direction,
                path=str(path),
            )
            return False

        # User chose "Always" previously
        if self.always_sync:
            return True

        # Prompt user (this would be interactive in CLI)
        # For now, return True (will be replaced with actual prompt in CLI)
        return True

    def _find_pair_for_local_path(self, local_path: Path) -> SyncPair | None:
        """Find sync pair for local path.

        Args:
            local_path: Local file path (relative to base_path)

        Returns:
            Sync pair or None if not found
        """
        for pair in self.sync_pairs:
            if Path(pair.local_path) == local_path:
                return pair
        return None

    async def _save_updated_pair(self, pair: SyncPair) -> None:
        """Save updated sync pair to metadata.

        Args:
            pair: Updated sync pair
        """
        try:
            metadata = await self.metadata_store.load()
            pairs = metadata.get("pairs", [])

            # Update pair in list
            for i, p in enumerate(pairs):
                if p["id"] == pair.id:
                    pairs[i] = pair.to_dict()
                    break

            metadata["pairs"] = pairs
            await self.metadata_store.save(metadata)

        except Exception as e:
            logger.error("error_saving_metadata", error=str(e))

    async def start(self) -> None:
        """Start watching for changes."""
        if self.is_running:
            logger.warning("watch_service_already_running")
            return

        # Store event loop for thread-safe async execution
        self.event_loop = asyncio.get_running_loop()

        # Load sync pairs
        await self.load_sync_pairs()

        if not self.sync_pairs:
            raise RuntimeError(
                "No sync pairs found. Run 'docsync init' first."
            )

        # Start file watcher
        self.file_watcher = FileWatcher(
            base_path=self.base_path,
            on_change_callback=self._handle_local_change,
            debounce_seconds=self.debounce_seconds,
        )
        self.file_watcher.start()

        # Start Notion poller
        notion_client = AsyncClient(auth=self.notion_token)
        self.notion_poller = NotionPoller(
            notion_client=notion_client,
            sync_pairs=self.sync_pairs,
            poll_interval_seconds=self.poll_interval,
        )
        self.notion_poller.start(self._process_remote_change)

        self.is_running = True

        logger.info(
            "watch_service_started",
            pairs_count=len(self.sync_pairs),
            mode=self.mode,
        )

    async def stop(self) -> None:
        """Stop watching for changes."""
        if not self.is_running:
            logger.warning("watch_service_not_running")
            return

        # Stop file watcher
        if self.file_watcher:
            self.file_watcher.stop()

        # Stop Notion poller
        if self.notion_poller:
            await self.notion_poller.stop()

        self.is_running = False

        logger.info("watch_service_stopped")

    async def __aenter__(self) -> WatchService:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.stop()
