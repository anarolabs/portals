"""High-level sync service for orchestrating sync operations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from portals.adapters.local import LocalFileAdapter
from portals.adapters.notion.adapter import NotionAdapter
from portals.core.exceptions import ConflictError, MetadataError, SyncError
from portals.core.metadata_store import MetadataStore
from portals.core.models import SyncPair, SyncResult, SyncStatus
from portals.core.sync_engine import SyncEngine

logger = logging.getLogger(__name__)


class SyncSummary:
    """Summary of sync operations."""

    def __init__(self) -> None:
        """Initialize summary."""
        self.total = 0
        self.success = 0
        self.no_changes = 0
        self.conflicts = 0
        self.errors = 0
        self.results: list[SyncResult] = []
        self.conflict_pairs: list[SyncPair] = []
        self.error_messages: list[str] = []

    def add_result(self, result: SyncResult, pair: SyncPair | None = None) -> None:
        """Add a sync result to summary.

        Args:
            result: Sync result
            pair: Optional sync pair (for conflicts)
        """
        self.total += 1
        self.results.append(result)

        if result.status == SyncStatus.SUCCESS:
            self.success += 1
        elif result.status == SyncStatus.NO_CHANGES:
            self.no_changes += 1
        elif result.status == SyncStatus.CONFLICT:
            self.conflicts += 1
            if pair:
                self.conflict_pairs.append(pair)
        elif result.status == SyncStatus.ERROR:
            self.errors += 1
            if result.error:
                self.error_messages.append(str(result.error))

    @property
    def has_conflicts(self) -> bool:
        """Check if there are any conflicts."""
        return self.conflicts > 0

    @property
    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return self.errors > 0


class SyncService:
    """High-level service for sync operations.

    Orchestrates syncing between local files and remote documents,
    managing metadata store and handling conflicts.
    """

    def __init__(
        self,
        base_path: str | Path,
        notion_token: str | None = None,
    ) -> None:
        """Initialize sync service.

        Args:
            base_path: Base directory path
            notion_token: Optional Notion API token
        """
        self.base_path = Path(base_path).resolve()
        self.metadata_store = MetadataStore(base_path=self.base_path)

        # Initialize adapters
        self.local_adapter = LocalFileAdapter(base_path=str(self.base_path))

        # Initialize remote adapter based on config
        self.notion_token = notion_token
        self.notion_adapter: NotionAdapter | None = None

        if notion_token:
            self.notion_adapter = NotionAdapter(api_token=notion_token)

        # Sync engine will be created when needed
        self.sync_engine: SyncEngine | None = None

    async def sync_all(
        self,
        force_direction: str | None = None,
    ) -> SyncSummary:
        """Sync all configured pairs.

        Args:
            force_direction: Optional force direction ("push" or "pull")

        Returns:
            SyncSummary with results

        Raises:
            MetadataError: If metadata cannot be loaded
        """
        logger.info("Starting sync for all pairs")

        # Load metadata
        if not self.metadata_store.exists():
            raise MetadataError(f"No metadata found at {self.base_path}. Run 'docsync init' first.")

        metadata = await self.metadata_store.load()
        pairs = metadata.get("pairs", [])

        # Handle both list and dict formats
        if isinstance(pairs, dict):
            pairs = list(pairs.values())

        if not pairs:
            logger.info("No sync pairs found")
            return SyncSummary()

        # Initialize sync engine
        await self._ensure_sync_engine()

        # Sync each pair
        summary = SyncSummary()

        for pair_data in pairs:
            pair = SyncPair.from_dict(pair_data)
            result = await self._sync_pair_safe(pair, force_direction)
            summary.add_result(result, pair if result.status == SyncStatus.CONFLICT else None)

        # Save updated metadata
        await self._save_pairs(pairs)

        logger.info(
            f"Sync complete: {summary.success} success, "
            f"{summary.no_changes} no changes, "
            f"{summary.conflicts} conflicts, "
            f"{summary.errors} errors"
        )

        return summary

    async def sync_file(
        self,
        file_path: str | Path,
        force_direction: str | None = None,
    ) -> SyncResult:
        """Sync a specific file.

        Args:
            file_path: Path to file (relative to base_path)
            force_direction: Optional force direction ("push" or "pull")

        Returns:
            SyncResult

        Raises:
            MetadataError: If metadata cannot be loaded or pair not found
        """
        file_path = Path(file_path)
        if file_path.is_absolute():
            file_path = file_path.relative_to(self.base_path)

        logger.info(f"Syncing file: {file_path}")

        # Load metadata
        if not self.metadata_store.exists():
            raise MetadataError(f"No metadata found at {self.base_path}. Run 'docsync init' first.")

        metadata = await self.metadata_store.load()
        pairs = metadata.get("pairs", [])

        # Handle both list and dict formats
        if isinstance(pairs, dict):
            pairs = list(pairs.values())

        # Find pair for this file
        pair_data = next(
            (p for p in pairs if Path(p["local_path"]) == file_path),
            None,
        )

        if not pair_data:
            raise MetadataError(f"No sync pair found for {file_path}")

        pair = SyncPair.from_dict(pair_data)

        # Initialize sync engine
        await self._ensure_sync_engine()

        # Sync the pair
        result = await self._sync_pair_safe(pair, force_direction)

        # Save updated metadata
        await self._save_pairs(pairs)

        return result

    async def _sync_pair_safe(
        self,
        pair: SyncPair,
        force_direction: str | None = None,
    ) -> SyncResult:
        """Sync a pair with error handling.

        Args:
            pair: Sync pair
            force_direction: Optional force direction

        Returns:
            SyncResult (always returns, never raises)
        """
        try:
            if not self.sync_engine:
                raise SyncError("Sync engine not initialized")

            return await self.sync_engine.sync_pair(pair, force_direction)

        except ConflictError as e:
            logger.warning(f"Conflict for {pair.local_path}: {e}")
            return SyncResult(
                status=SyncStatus.CONFLICT,
                message=str(e),
                local_path=pair.local_path,
                remote_uri=pair.remote_uri,
                error=e,
            )

        except Exception as e:
            logger.error(f"Error syncing {pair.local_path}: {e}")
            return SyncResult(
                status=SyncStatus.ERROR,
                message=f"Sync failed: {e}",
                local_path=pair.local_path,
                remote_uri=pair.remote_uri,
                error=e,
            )

    async def _ensure_sync_engine(self) -> None:
        """Ensure sync engine is initialized with correct remote adapter."""
        if self.sync_engine:
            return

        # Get platform from metadata
        metadata = await self.metadata_store.load()
        mode = metadata.get("config", {}).get("mode", "")

        if "notion" in mode:
            if not self.notion_adapter:
                if not self.notion_token:
                    raise MetadataError(
                        "Notion token required. Set NOTION_API_TOKEN environment variable."
                    )
                self.notion_adapter = NotionAdapter(api_token=self.notion_token)

            self.sync_engine = SyncEngine(
                local_adapter=self.local_adapter,
                remote_adapter=self.notion_adapter,
            )
        else:
            raise MetadataError(f"Unsupported mode: {mode}")

    async def _save_pairs(self, pairs: list[dict[str, Any]]) -> None:
        """Save updated sync pairs to metadata.

        Args:
            pairs: List of sync pair dictionaries
        """
        metadata = await self.metadata_store.load()
        metadata["pairs"] = pairs
        await self.metadata_store.save(metadata)

    async def get_status(self) -> dict[str, Any]:
        """Get sync status for all pairs.

        Returns:
            Dictionary with status information
        """
        if not self.metadata_store.exists():
            return {
                "initialized": False,
                "pairs": [],
            }

        metadata = await self.metadata_store.load()
        pairs = metadata.get("pairs", [])

        return {
            "initialized": True,
            "mode": metadata.get("config", {}).get("mode"),
            "pairs_count": len(pairs),
            "pairs": [
                {
                    "local_path": p["local_path"],
                    "remote_uri": p["remote_uri"],
                    "has_conflict": p.get("state", {}).get("has_conflict", False),
                    "last_sync": p.get("state", {}).get("last_sync"),
                }
                for p in pairs
            ],
        }
