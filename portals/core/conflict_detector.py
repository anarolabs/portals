"""Conflict detection for sync operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from portals.core.models import SyncStatus

logger = logging.getLogger(__name__)


@dataclass
class SyncDecision:
    """Decision about how to sync a document."""

    status: SyncStatus
    reason: str
    local_hash: str
    remote_hash: str
    base_hash: str

    @property
    def should_push(self) -> bool:
        """Check if local changes should be pushed to remote."""
        return self.status == SyncStatus.SUCCESS and "push" in self.reason.lower()

    @property
    def should_pull(self) -> bool:
        """Check if remote changes should be pulled to local."""
        return self.status == SyncStatus.SUCCESS and "pull" in self.reason.lower()

    @property
    def has_conflict(self) -> bool:
        """Check if there's a conflict."""
        return self.status == SyncStatus.CONFLICT


class ConflictDetector:
    """Detects conflicts and determines sync direction using 3-way merge.

    Uses the classic 3-way merge algorithm:
    - Compare local, remote, and base (last synced) versions
    - Determine if changes can be automatically synced or need manual resolution
    """

    def detect(
        self,
        local_hash: str,
        remote_hash: str,
        base_hash: str,
    ) -> SyncDecision:
        """Detect conflicts and determine sync direction.

        Args:
            local_hash: Hash of local file content
            remote_hash: Hash of remote document content
            base_hash: Hash of content at last sync (baseline)

        Returns:
            SyncDecision indicating what action to take
        """
        logger.debug(
            f"Detecting conflict: local={local_hash[:8]}, "
            f"remote={remote_hash[:8]}, base={base_hash[:8]}"
        )

        # Case 1: Nothing changed
        if local_hash == base_hash and remote_hash == base_hash:
            return SyncDecision(
                status=SyncStatus.NO_CHANGES,
                reason="No changes on either side",
                local_hash=local_hash,
                remote_hash=remote_hash,
                base_hash=base_hash,
            )

        # Case 2: Only local changed (push to remote)
        if local_hash != base_hash and remote_hash == base_hash:
            return SyncDecision(
                status=SyncStatus.SUCCESS,
                reason="Local changed, remote unchanged - push required",
                local_hash=local_hash,
                remote_hash=remote_hash,
                base_hash=base_hash,
            )

        # Case 3: Only remote changed (pull from remote)
        if local_hash == base_hash and remote_hash != base_hash:
            return SyncDecision(
                status=SyncStatus.SUCCESS,
                reason="Remote changed, local unchanged - pull required",
                local_hash=local_hash,
                remote_hash=remote_hash,
                base_hash=base_hash,
            )

        # Case 4: Both changed to the same content (no conflict, just update base)
        if local_hash == remote_hash:
            return SyncDecision(
                status=SyncStatus.SUCCESS,
                reason="Identical changes on both sides - update base hash only",
                local_hash=local_hash,
                remote_hash=remote_hash,
                base_hash=base_hash,
            )

        # Case 5: Both changed differently (conflict)
        return SyncDecision(
            status=SyncStatus.CONFLICT,
            reason="Both local and remote changed differently - manual resolution required",
            local_hash=local_hash,
            remote_hash=remote_hash,
            base_hash=base_hash,
        )

    def detect_from_pair_state(
        self,
        local_current_hash: str,
        remote_current_hash: str,
        last_synced_hash: str,
    ) -> SyncDecision:
        """Convenience method to detect conflicts from sync pair state.

        Args:
            local_current_hash: Current hash of local file
            remote_current_hash: Current hash of remote document
            last_synced_hash: Hash at time of last sync

        Returns:
            SyncDecision indicating what action to take
        """
        return self.detect(
            local_hash=local_current_hash,
            remote_hash=remote_current_hash,
            base_hash=last_synced_hash,
        )
