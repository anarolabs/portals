"""Metadata store for managing sync state."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from portals.core.exceptions import MetadataError
from portals.core.models import ConflictResolution, SyncDirection, SyncPair, SyncPairState


class MetadataStore:
    """Manages sync metadata stored in .portals/ directory.

    Stores information about file pairs, sync status, and configuration.
    """

    METADATA_DIR = ".portals"
    METADATA_FILE = "metadata.json"

    def __init__(self, base_path: str | Path) -> None:
        """Initialize metadata store.

        Args:
            base_path: Base directory containing .portals/
        """
        self.base_path = Path(base_path)
        self.metadata_dir = self.base_path / self.METADATA_DIR
        self.metadata_file = self.metadata_dir / self.METADATA_FILE

    async def initialize(self) -> None:
        """Initialize .portals/ directory and metadata file.

        Creates the directory if it doesn't exist.
        Creates empty metadata file if it doesn't exist.

        Raises:
            MetadataError: If initialization fails
        """
        try:
            # Create .portals directory
            self.metadata_dir.mkdir(parents=True, exist_ok=True)

            # Create empty metadata file if it doesn't exist
            if not self.metadata_file.exists():
                await self._write_metadata({"version": "1.0", "pairs": {}, "config": {}})

        except Exception as e:
            raise MetadataError(f"Failed to initialize metadata store: {e}") from e

    async def load(self) -> dict[str, Any]:
        """Load metadata from file.

        Returns:
            Metadata dictionary

        Raises:
            MetadataError: If loading fails
        """
        try:
            if not self.metadata_file.exists():
                # Return empty structure if file doesn't exist
                return {"version": "1.0", "pairs": {}, "config": {}}

            async with aiofiles.open(self.metadata_file, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)

            # Validate structure
            if not isinstance(data, dict):
                raise MetadataError("Metadata file is not a valid JSON object")

            if "pairs" not in data:
                data["pairs"] = {}

            if "config" not in data:
                data["config"] = {}

            return data

        except json.JSONDecodeError as e:
            raise MetadataError(f"Invalid JSON in metadata file: {e}") from e
        except Exception as e:
            raise MetadataError(f"Failed to load metadata: {e}") from e

    async def save(self, data: dict[str, Any]) -> None:
        """Save metadata to file atomically.

        Uses atomic write (temp file + rename) to ensure data integrity.

        Args:
            data: Metadata dictionary to save

        Raises:
            MetadataError: If saving fails
        """
        try:
            await self._write_metadata(data)
        except Exception as e:
            raise MetadataError(f"Failed to save metadata: {e}") from e

    async def get_pair(self, pair_id: str) -> SyncPair | None:
        """Get sync pair by ID.

        Args:
            pair_id: Pair ID

        Returns:
            SyncPair object or None if not found
        """
        data = await self.load()
        pair_data = data["pairs"].get(pair_id)

        if not pair_data:
            return None

        return self._dict_to_pair(pair_data)

    async def add_pair(self, pair: SyncPair) -> None:
        """Add or update sync pair.

        Args:
            pair: SyncPair object to add

        Raises:
            MetadataError: If adding fails
        """
        data = await self.load()
        data["pairs"][pair.id] = pair.to_dict()
        await self.save(data)

    async def remove_pair(self, pair_id: str) -> None:
        """Remove sync pair.

        Args:
            pair_id: Pair ID to remove

        Raises:
            MetadataError: If removal fails
        """
        data = await self.load()

        if pair_id not in data["pairs"]:
            raise MetadataError(f"Pair not found: {pair_id}")

        del data["pairs"][pair_id]
        await self.save(data)

    async def list_pairs(self) -> list[SyncPair]:
        """List all sync pairs.

        Returns:
            List of SyncPair objects
        """
        data = await self.load()
        pairs = []

        for pair_data in data["pairs"].values():
            pairs.append(self._dict_to_pair(pair_data))

        return pairs

    async def update_pair_state(self, pair_id: str, state: SyncPairState) -> None:
        """Update sync pair state.

        Args:
            pair_id: Pair ID
            state: New state

        Raises:
            MetadataError: If update fails
        """
        data = await self.load()

        if pair_id not in data["pairs"]:
            raise MetadataError(f"Pair not found: {pair_id}")

        data["pairs"][pair_id]["state"] = state.to_dict()
        await self.save(data)

    async def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Config value or default
        """
        data = await self.load()
        return data["config"].get(key, default)

    async def set_config(self, key: str, value: Any) -> None:
        """Set configuration value.

        Args:
            key: Config key
            value: Config value

        Raises:
            MetadataError: If setting fails
        """
        data = await self.load()
        data["config"][key] = value
        await self.save(data)

    def exists(self) -> bool:
        """Check if metadata store exists.

        Returns:
            True if .portals/ directory exists
        """
        return self.metadata_dir.exists()

    async def _write_metadata(self, data: dict[str, Any]) -> None:
        """Write metadata atomically using temp file + rename.

        Args:
            data: Metadata to write

        Raises:
            MetadataError: If writing fails
        """
        try:
            # Ensure directory exists
            self.metadata_dir.mkdir(parents=True, exist_ok=True)

            # Write to temporary file
            temp_file = self.metadata_dir / f"{self.METADATA_FILE}.tmp"

            async with aiofiles.open(temp_file, "w", encoding="utf-8") as f:
                content = json.dumps(data, indent=2, sort_keys=True)
                await f.write(content)

            # Atomic rename
            temp_file.replace(self.metadata_file)

        except Exception as e:
            # Clean up temp file if it exists
            temp_file = self.metadata_dir / f"{self.METADATA_FILE}.tmp"
            if temp_file.exists():
                temp_file.unlink()
            raise MetadataError(f"Failed to write metadata: {e}") from e

    def _dict_to_pair(self, data: dict[str, Any]) -> SyncPair:
        """Convert dictionary to SyncPair object.

        Args:
            data: Dictionary representation

        Returns:
            SyncPair object
        """
        # Parse state if present
        state = None
        if data.get("state"):
            state_data = data["state"]
            state = SyncPairState(
                local_hash=state_data["local_hash"],
                remote_hash=state_data["remote_hash"],
                last_synced_hash=state_data["last_synced_hash"],
                last_sync=datetime.fromisoformat(state_data["last_sync"]),
                has_conflict=state_data.get("has_conflict", False),
                last_error=state_data.get("last_error"),
            )

        return SyncPair(
            id=data["id"],
            local_path=data["local_path"],
            remote_uri=data["remote_uri"],
            remote_platform=data["remote_platform"],
            created_at=datetime.fromisoformat(data["created_at"]),
            sync_direction=SyncDirection(data.get("sync_direction", "bidirectional")),
            conflict_resolution=ConflictResolution(data.get("conflict_resolution", "manual")),
            state=state,
        )
