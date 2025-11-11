"""Initialization service for setting up mirror mode."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from portals.adapters.local import LocalFileAdapter
from portals.adapters.notion.adapter import NotionAdapter
from portals.adapters.notion.hierarchy import NotionHierarchyManager
from portals.core.directory_scanner import DirectoryScanner
from portals.core.exceptions import PortalsError
from portals.core.metadata_store import MetadataStore
from portals.core.models import SyncDirection, SyncPair, SyncPairState

logger = logging.getLogger(__name__)


class InitResult:
    """Result of initialization operation."""

    def __init__(
        self,
        success: bool,
        files_synced: int = 0,
        pages_created: int = 0,
        errors: list[str] | None = None,
    ) -> None:
        """Initialize result.

        Args:
            success: Whether initialization succeeded
            files_synced: Number of files synced
            pages_created: Number of Notion pages created
            errors: List of error messages
        """
        self.success = success
        self.files_synced = files_synced
        self.pages_created = pages_created
        self.errors = errors or []


class InitService:
    """Service for initializing mirror mode.

    Handles the complete workflow:
    1. Scan local directory for markdown files
    2. Create Notion page hierarchy
    3. Upload file content
    4. Save metadata and sync pairs
    """

    def __init__(
        self,
        base_path: str | Path,
        notion_token: str,
        root_page_id: str,
    ) -> None:
        """Initialize service.

        Args:
            base_path: Local directory to sync
            notion_token: Notion API token
            root_page_id: Notion page ID to use as root
        """
        self.base_path = Path(base_path).resolve()
        self.root_page_id = root_page_id

        # Initialize adapters and services
        self.local_adapter = LocalFileAdapter()
        self.notion_adapter = NotionAdapter(api_token=notion_token)
        self.directory_scanner = DirectoryScanner(base_path=self.base_path)
        self.hierarchy_manager = NotionHierarchyManager(root_page_id=root_page_id)
        self.metadata_store = MetadataStore(base_path=self.base_path)

    async def initialize_mirror_mode(
        self,
        dry_run: bool = False,
    ) -> InitResult:
        """Initialize mirror mode.

        Args:
            dry_run: If True, don't actually create pages or write metadata

        Returns:
            InitResult with operation details

        Raises:
            PortalsError: If initialization fails
        """
        try:
            logger.info(f"Initializing mirror mode for {self.base_path}")

            # 1. Check if already initialized
            if self.metadata_store.exists():
                raise PortalsError(
                    f"Directory already initialized. "
                    f"Remove {self.base_path / '.docsync'} to reinitialize."
                )

            # 2. Scan directory for markdown files
            logger.info("Scanning directory for markdown files...")
            files = self.directory_scanner.scan_markdown()
            logger.info(f"Found {len(files)} markdown files")

            if not files:
                return InitResult(
                    success=True,
                    files_synced=0,
                    pages_created=0,
                    errors=["No markdown files found"],
                )

            # 3. Create Notion pages and upload content
            files_synced = 0
            pages_created = 0
            errors: list[str] = []

            for file_info in files:
                try:
                    # Read local file
                    local_uri = f"file://{file_info.path}"
                    doc = await self.local_adapter.read(local_uri)

                    # Determine parent page ID based on directory structure
                    parent_id = self.hierarchy_manager.get_parent_for_path(file_info.relative_path)

                    if not dry_run:
                        # Create Notion page
                        notion_uri = await self.notion_adapter.create(
                            uri="notion://",
                            doc=doc,
                            parent_id=parent_id,
                        )

                        # Extract page ID from URI
                        parsed = self.notion_adapter.parse_uri(notion_uri)
                        page_id = parsed.identifier

                        # Register in hierarchy manager
                        self.hierarchy_manager.register_page(
                            local_path=file_info.relative_path,
                            page_id=page_id,
                            parent_id=parent_id,
                        )

                        pages_created += 1

                    files_synced += 1
                    logger.info(f"Synced: {file_info.relative_path}")

                except Exception as e:
                    error_msg = f"Failed to sync {file_info.relative_path}: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

            # 4. Save metadata
            if not dry_run:
                await self._save_metadata()
                logger.info("Metadata saved")

            return InitResult(
                success=len(errors) == 0,
                files_synced=files_synced,
                pages_created=pages_created,
                errors=errors,
            )

        except PortalsError:
            raise
        except Exception as e:
            raise PortalsError(f"Failed to initialize mirror mode: {e}") from e

    async def _save_metadata(self) -> None:
        """Save metadata and sync pairs."""
        # Initialize metadata store
        await self.metadata_store.initialize()

        # Set configuration
        await self.metadata_store.set_config("mode", "notion-mirror")
        await self.metadata_store.set_config("root_page_id", self.root_page_id)
        await self.metadata_store.set_config("base_path", str(self.base_path))

        # Save hierarchy
        hierarchy_data = self.hierarchy_manager.to_dict()
        await self.metadata_store.set_config("hierarchy", hierarchy_data)

        # Create sync pairs for each file
        for local_path, page_id in self.hierarchy_manager.list_pages():
            # Read file to get current hash
            full_path = self.base_path / local_path
            local_uri = f"file://{full_path}"
            doc = await self.local_adapter.read(local_uri)

            # Get remote metadata
            notion_uri = f"notion://{page_id}"
            remote_meta = await self.notion_adapter.get_metadata(notion_uri)

            # Parse last_modified timestamp
            now = datetime.now()
            last_modified_dt = now
            if remote_meta.last_modified:
                try:
                    last_modified_dt = datetime.fromisoformat(
                        remote_meta.last_modified.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    last_modified_dt = now

            # Create sync pair
            pair = SyncPair(
                id=str(uuid.uuid4()),
                local_path=local_path,
                remote_uri=notion_uri,
                remote_platform="notion",
                created_at=now,
                sync_direction=SyncDirection.BIDIRECTIONAL,
                state=SyncPairState(
                    local_hash=doc.content_hash or "",
                    remote_hash=remote_meta.content_hash,
                    last_synced_hash=doc.content_hash or "",  # Initial sync
                    last_sync=last_modified_dt,
                ),
            )

            await self.metadata_store.add_pair(pair)

    async def get_status(self) -> dict[str, Any]:
        """Get initialization status.

        Returns:
            Dictionary with status information
        """
        return {
            "initialized": self.metadata_store.exists(),
            "base_path": str(self.base_path),
            "root_page_id": self.root_page_id,
        }
