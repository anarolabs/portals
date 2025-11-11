"""Base adapter interface for all platform integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from portals.core.models import Document


@dataclass
class RemoteMetadata:
    """Metadata about a remote document."""

    uri: str
    content_hash: str
    last_modified: str  # ISO 8601 timestamp
    exists: bool = True


@dataclass
class PlatformURI:
    """Parsed platform-specific URI."""

    platform: str  # "notion", "gdocs", "obsidian", "file"
    identifier: str  # Page ID, Doc ID, file path, etc.
    raw_uri: str

    def __str__(self) -> str:
        """String representation."""
        return self.raw_uri


class DocumentAdapter(ABC):
    """Base adapter interface for all platforms.

    All platform adapters (Notion, Google Docs, Obsidian, Local) must implement
    this interface. This ensures consistent behavior across all platforms.
    """

    @abstractmethod
    async def read(self, uri: str) -> Document:
        """Read document from platform.

        Args:
            uri: Platform-specific URI (e.g., "notion://abc123", "file:///path/to/file.md")

        Returns:
            Document object with content and metadata

        Raises:
            AdapterError: If document cannot be read
        """
        pass

    @abstractmethod
    async def write(self, uri: str, doc: Document) -> None:
        """Write document to platform.

        Args:
            uri: Platform-specific URI
            doc: Document to write

        Raises:
            AdapterError: If document cannot be written
        """
        pass

    @abstractmethod
    async def get_metadata(self, uri: str) -> RemoteMetadata:
        """Get document metadata without reading full content.

        Args:
            uri: Platform-specific URI

        Returns:
            RemoteMetadata with hash and timestamp

        Raises:
            AdapterError: If metadata cannot be retrieved
        """
        pass

    @abstractmethod
    async def exists(self, uri: str) -> bool:
        """Check if document exists.

        Args:
            uri: Platform-specific URI

        Returns:
            True if document exists, False otherwise
        """
        pass

    @abstractmethod
    def parse_uri(self, uri: str) -> PlatformURI:
        """Parse platform-specific URI.

        Args:
            uri: URI string to parse

        Returns:
            Parsed PlatformURI object

        Raises:
            ValueError: If URI is invalid
        """
        pass

    @abstractmethod
    async def create(self, uri: str, doc: Document, parent_id: str | None = None) -> str:
        """Create new document on platform.

        Args:
            uri: Platform-specific URI (may be partial, e.g., just filename)
            doc: Document to create
            parent_id: Optional parent ID for hierarchical platforms

        Returns:
            Full URI of created document

        Raises:
            AdapterError: If document cannot be created
        """
        pass

    @abstractmethod
    async def delete(self, uri: str) -> None:
        """Delete or archive document.

        Args:
            uri: Platform-specific URI

        Raises:
            AdapterError: If document cannot be deleted
        """
        pass
