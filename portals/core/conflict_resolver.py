"""Interactive conflict resolution."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

from portals.adapters.local import LocalFileAdapter
from portals.core.diff_generator import DiffGenerator
from portals.core.exceptions import SyncError
from portals.core.models import Document, SyncPair
from portals.core.sync_engine import SyncEngine

logger = logging.getLogger(__name__)


class ResolutionStrategy(str, Enum):
    """Conflict resolution strategies."""

    USE_LOCAL = "local"
    USE_REMOTE = "remote"
    MERGE_MANUAL = "manual"
    SHOW_DIFF = "diff"
    CANCEL = "cancel"


class ConflictResolver:
    """Interactive conflict resolver.

    Provides tools for users to resolve conflicts between local and remote versions.
    """

    def __init__(
        self,
        sync_engine: SyncEngine,
        local_adapter: LocalFileAdapter,
        diff_generator: DiffGenerator | None = None,
    ) -> None:
        """Initialize conflict resolver.

        Args:
            sync_engine: Sync engine for applying resolutions
            local_adapter: Local file adapter
            diff_generator: Optional diff generator (creates default if not provided)
        """
        self.sync_engine = sync_engine
        self.local_adapter = local_adapter
        self.diff_generator = diff_generator or DiffGenerator()

    async def resolve_conflict(
        self,
        pair: SyncPair,
        local_doc: Document,
        remote_doc: Document,
        strategy: ResolutionStrategy,
    ) -> bool:
        """Resolve a conflict using the specified strategy.

        Args:
            pair: Sync pair with conflict
            local_doc: Local document version
            remote_doc: Remote document version
            strategy: Resolution strategy to apply

        Returns:
            True if conflict was resolved successfully

        Raises:
            SyncError: If resolution fails
        """
        logger.info(f"Resolving conflict for {pair.local_path} using {strategy.value}")

        try:
            if strategy == ResolutionStrategy.USE_LOCAL:
                # Force push local version
                await self.sync_engine.push(pair)
                logger.info(f"Resolved conflict: used local version for {pair.local_path}")
                return True

            elif strategy == ResolutionStrategy.USE_REMOTE:
                # Force pull remote version
                await self.sync_engine.pull(pair)
                logger.info(f"Resolved conflict: used remote version for {pair.local_path}")
                return True

            elif strategy == ResolutionStrategy.MERGE_MANUAL:
                # Open editor with conflict markers
                merged_content = await self._manual_merge(
                    local_doc.content,
                    remote_doc.content,
                    pair.local_path,
                )

                if merged_content is None:
                    logger.info("Manual merge cancelled")
                    return False

                # Write merged content locally
                merged_doc = Document(
                    content=merged_content,
                    metadata=local_doc.metadata,
                )
                await self.local_adapter.write(f"file://{pair.local_path}", merged_doc)

                # Push merged version to remote
                await self.sync_engine.push(pair)
                logger.info(f"Resolved conflict: manual merge for {pair.local_path}")
                return True

            elif strategy == ResolutionStrategy.CANCEL:
                logger.info("Conflict resolution cancelled")
                return False

            else:
                raise SyncError(f"Unknown resolution strategy: {strategy}")

        except Exception as e:
            logger.error(f"Failed to resolve conflict: {e}")
            raise SyncError(f"Conflict resolution failed: {e}") from e

    async def _manual_merge(
        self,
        local_content: str,
        remote_content: str,
        file_path: str,
    ) -> str | None:
        """Open editor for manual conflict resolution.

        Args:
            local_content: Local file content
            remote_content: Remote document content
            file_path: Path to file (for context)

        Returns:
            Merged content, or None if cancelled
        """
        # Generate content with conflict markers
        conflict_content = self.diff_generator.generate_conflict_markers(
            local_content,
            remote_content,
            local_label="LOCAL",
            remote_label="REMOTE",
        )

        # Write to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=Path(file_path).suffix or ".md",
            delete=False,
            encoding="utf-8",
        ) as tmp_file:
            tmp_file.write(conflict_content)
            tmp_path = tmp_file.name

        try:
            # Open editor (respect EDITOR environment variable)
            editor = self._get_editor()
            subprocess.run([editor, tmp_path], check=True)

            # Read merged content
            with open(tmp_path, encoding="utf-8") as f:
                merged_content = f.read()

            # Check if user actually resolved conflicts
            if "<<<<<<< LOCAL" in merged_content:
                logger.warning("Conflict markers still present - merge may be incomplete")

            return merged_content

        finally:
            # Clean up temporary file
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

    def _get_editor(self) -> str:
        """Get editor command to use.

        Returns:
            Editor command
        """
        import os

        # Try environment variables
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")

        if editor:
            return editor

        # Try common editors
        for cmd in ["vim", "vi", "nano", "emacs"]:
            try:
                subprocess.run(
                    ["which", cmd],
                    check=True,
                    capture_output=True,
                )
                return cmd
            except subprocess.CalledProcessError:
                continue

        # Fallback
        return "vi"

    def get_conflict_info(
        self,
        local_doc: Document,
        remote_doc: Document,
    ) -> dict[str, Any]:
        """Get information about a conflict.

        Args:
            local_doc: Local document version
            remote_doc: Remote document version

        Returns:
            Dictionary with conflict information
        """
        summary = self.diff_generator.get_change_summary(
            local_doc.content,
            remote_doc.content,
        )

        return {
            "has_conflict": self.diff_generator.has_conflicts(
                local_doc.content,
                remote_doc.content,
            ),
            "local_modified": local_doc.metadata.modified_at,
            "remote_modified": remote_doc.metadata.modified_at,
            "changes": summary,
        }

    def format_diff_preview(
        self,
        local_doc: Document,
        remote_doc: Document,
        max_lines: int = 20,
    ) -> str:
        """Format a preview of the diff for display.

        Args:
            local_doc: Local document version
            remote_doc: Remote document version
            max_lines: Maximum lines to show

        Returns:
            Formatted diff preview
        """
        diff = self.diff_generator.generate_unified_diff(
            local_doc.content,
            remote_doc.content,
            local_label="LOCAL",
            remote_label="REMOTE",
        )

        lines = diff.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"]

        return "\n".join(lines)
