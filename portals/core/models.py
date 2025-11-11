"""Core data models for Portals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SyncStatus(Enum):
    """Status of a sync operation."""

    NO_CHANGES = "no_changes"
    PUSH = "push"
    PULL = "pull"
    CONFLICT = "conflict"
    SUCCESS = "success"
    ERROR = "error"


class SyncDirection(Enum):
    """Direction of sync."""

    BIDIRECTIONAL = "bidirectional"
    PUSH_ONLY = "push_only"
    PULL_ONLY = "pull_only"


class ConflictResolution(Enum):
    """Conflict resolution strategy."""

    MANUAL = "manual"
    LOCAL_WINS = "local_wins"
    REMOTE_WINS = "remote_wins"
    LATEST_WINS = "latest_wins"


@dataclass
class DocumentMetadata:
    """Metadata for a document."""

    title: str
    created_at: datetime
    modified_at: datetime
    tags: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "modified_at": self.modified_at.isoformat(),
            "tags": self.tags,
            "properties": self.properties,
        }


@dataclass
class Document:
    """Internal document representation.

    This is the common format that all adapters convert to/from.
    """

    content: str  # Markdown content
    metadata: DocumentMetadata
    content_hash: str | None = None  # SHA-256 hash of content

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "content": self.content,
            "metadata": self.metadata.to_dict(),
            "content_hash": self.content_hash,
        }


@dataclass
class SyncPairState:
    """State of a sync pair."""

    local_hash: str
    remote_hash: str
    last_synced_hash: str
    last_sync: datetime
    has_conflict: bool = False
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "local_hash": self.local_hash,
            "remote_hash": self.remote_hash,
            "last_synced_hash": self.last_synced_hash,
            "last_sync": self.last_sync.isoformat(),
            "has_conflict": self.has_conflict,
            "last_error": self.last_error,
        }


@dataclass
class SyncPair:
    """A pairing of local file and remote document."""

    id: str
    local_path: str
    remote_uri: str
    remote_platform: str  # "notion", "gdocs", "obsidian"
    created_at: datetime
    sync_direction: SyncDirection = SyncDirection.BIDIRECTIONAL
    conflict_resolution: ConflictResolution = ConflictResolution.MANUAL
    state: SyncPairState | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "local_path": self.local_path,
            "remote_uri": self.remote_uri,
            "remote_platform": self.remote_platform,
            "created_at": self.created_at.isoformat(),
            "sync_direction": self.sync_direction.value,
            "conflict_resolution": self.conflict_resolution.value,
            "state": self.state.to_dict() if self.state else None,
        }


@dataclass
class SyncResult:
    """Result of a sync operation."""

    status: SyncStatus
    message: str
    local_path: str | None = None
    remote_uri: str | None = None
    error: Exception | None = None

    def is_success(self) -> bool:
        """Check if sync was successful."""
        return self.status in (SyncStatus.SUCCESS, SyncStatus.NO_CHANGES)

    def is_conflict(self) -> bool:
        """Check if sync resulted in conflict."""
        return self.status == SyncStatus.CONFLICT
