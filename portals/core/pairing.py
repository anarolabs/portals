"""Pairing management for Portals.

Handles tracking relationships between local files and remote documents
across different platforms (Google Docs, Notion, etc.).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Literal


PlatformType = Literal["gdocs", "notion"]


@dataclass
class Pairing:
    """Represents a pairing between a local file and remote document."""

    local_path: str  # Absolute path to local markdown file
    platform: PlatformType  # Platform type
    remote_id: str  # Remote document ID (Google Doc ID, Notion page ID, etc.)
    account: str | None = None  # Account identifier (for multi-account platforms)
    last_sync: str | None = None  # ISO format timestamp of last sync
    local_hash: str | None = None  # Hash of local file at last sync
    remote_hash: str | None = None  # Hash of remote content at last sync

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Pairing:
        """Create from dictionary."""
        return cls(**data)


class PairingManager:
    """Manages pairings between local files and remote documents."""

    def __init__(self, config_dir: str | None = None):
        """Initialize pairing manager.

        Args:
            config_dir: Directory to store pairing config (defaults to .portals in cwd)
        """
        self.config_dir = Path(config_dir or ".portals")
        self.pairings_file = self.config_dir / "pairings.json"
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load_pairings(self) -> dict[str, Pairing]:
        """Load all pairings from disk.

        Returns:
            Dictionary mapping local paths to Pairing objects
        """
        if not self.pairings_file.exists():
            return {}

        with open(self.pairings_file, 'r') as f:
            data = json.load(f)

        return {
            path: Pairing.from_dict(pairing_data)
            for path, pairing_data in data.items()
        }

    def _save_pairings(self, pairings: dict[str, Pairing]):
        """Save pairings to disk.

        Args:
            pairings: Dictionary mapping local paths to Pairing objects
        """
        data = {
            path: pairing.to_dict()
            for path, pairing in pairings.items()
        }

        with open(self.pairings_file, 'w') as f:
            json.dump(data, f, indent=2)

    def add_pairing(
        self,
        local_path: str,
        platform: PlatformType,
        remote_id: str,
        account: str | None = None
    ) -> Pairing:
        """Add a new pairing.

        Args:
            local_path: Path to local file
            platform: Platform type
            remote_id: Remote document ID
            account: Optional account identifier

        Returns:
            Created Pairing object
        """
        # Normalize local path to absolute
        local_path = str(Path(local_path).resolve())

        # Create pairing
        pairing = Pairing(
            local_path=local_path,
            platform=platform,
            remote_id=remote_id,
            account=account,
            last_sync=None,
            local_hash=None,
            remote_hash=None
        )

        # Load, update, and save
        pairings = self._load_pairings()
        pairings[local_path] = pairing
        self._save_pairings(pairings)

        return pairing

    def get_pairing(self, local_path: str) -> Pairing | None:
        """Get pairing for a local file.

        Args:
            local_path: Path to local file

        Returns:
            Pairing object if exists, None otherwise
        """
        local_path = str(Path(local_path).resolve())
        pairings = self._load_pairings()
        return pairings.get(local_path)

    def remove_pairing(self, local_path: str) -> bool:
        """Remove a pairing.

        Args:
            local_path: Path to local file

        Returns:
            True if pairing was removed, False if it didn't exist
        """
        local_path = str(Path(local_path).resolve())
        pairings = self._load_pairings()

        if local_path in pairings:
            del pairings[local_path]
            self._save_pairings(pairings)
            return True

        return False

    def list_pairings(self, platform: PlatformType | None = None) -> list[Pairing]:
        """List all pairings, optionally filtered by platform.

        Args:
            platform: Optional platform filter

        Returns:
            List of Pairing objects
        """
        pairings = self._load_pairings()

        if platform:
            return [p for p in pairings.values() if p.platform == platform]

        return list(pairings.values())

    def update_sync_state(
        self,
        local_path: str,
        local_hash: str | None = None,
        remote_hash: str | None = None
    ):
        """Update sync state for a pairing.

        Args:
            local_path: Path to local file
            local_hash: Optional hash of local content
            remote_hash: Optional hash of remote content
        """
        local_path = str(Path(local_path).resolve())
        pairings = self._load_pairings()

        if local_path not in pairings:
            raise ValueError(f"No pairing found for {local_path}")

        pairing = pairings[local_path]
        pairing.last_sync = datetime.now().isoformat()

        if local_hash is not None:
            pairing.local_hash = local_hash

        if remote_hash is not None:
            pairing.remote_hash = remote_hash

        self._save_pairings(pairings)
