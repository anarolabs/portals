"""Tests for MetadataStore."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from portals.core.exceptions import MetadataError
from portals.core.metadata_store import MetadataStore
from portals.core.models import ConflictResolution, SyncDirection, SyncPair, SyncPairState


@pytest.fixture
def store(tmp_path: Path) -> MetadataStore:
    """Create MetadataStore with temporary directory."""
    return MetadataStore(tmp_path)


@pytest.fixture
def sample_pair() -> SyncPair:
    """Create sample sync pair for testing."""
    return SyncPair(
        id="pair-123",
        local_path="/path/to/local.md",
        remote_uri="notion://page-456",
        remote_platform="notion",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        sync_direction=SyncDirection.BIDIRECTIONAL,
        conflict_resolution=ConflictResolution.MANUAL,
        state=SyncPairState(
            local_hash="abc123",
            remote_hash="def456",
            last_synced_hash="abc123",
            last_sync=datetime(2024, 1, 2, 12, 0, 0),
            has_conflict=False,
            last_error=None,
        ),
    )


class TestMetadataStore:
    """Tests for MetadataStore."""

    async def test_initialize(self, store: MetadataStore, tmp_path: Path) -> None:
        """Test initialization creates .portals/ directory and metadata file."""
        await store.initialize()

        # Verify directory exists
        assert store.metadata_dir.exists()
        assert store.metadata_dir.is_dir()

        # Verify metadata file exists
        assert store.metadata_file.exists()
        assert store.metadata_file.is_file()

        # Verify content
        data = await store.load()
        assert data["version"] == "1.0"
        assert data["pairs"] == {}
        assert data["config"] == {}

    async def test_initialize_idempotent(self, store: MetadataStore) -> None:
        """Test that calling initialize multiple times is safe."""
        await store.initialize()
        await store.initialize()  # Should not raise

        assert store.metadata_dir.exists()
        assert store.metadata_file.exists()

    async def test_load_nonexistent(self, store: MetadataStore) -> None:
        """Test loading when metadata file doesn't exist."""
        data = await store.load()

        # Should return empty structure
        assert data["version"] == "1.0"
        assert data["pairs"] == {}
        assert data["config"] == {}

    async def test_save_and_load(self, store: MetadataStore) -> None:
        """Test saving and loading metadata."""
        await store.initialize()

        # Save data
        data = {
            "version": "1.0",
            "pairs": {"test": {"id": "test"}},
            "config": {"key": "value"},
        }
        await store.save(data)

        # Load and verify
        loaded = await store.load()
        assert loaded == data

    async def test_atomic_write(self, store: MetadataStore, tmp_path: Path) -> None:
        """Test that writes are atomic (temp file + rename)."""
        await store.initialize()

        data = {"version": "1.0", "pairs": {}, "config": {}}
        await store.save(data)

        # Temp file should not exist after write
        temp_file = store.metadata_dir / f"{store.METADATA_FILE}.tmp"
        assert not temp_file.exists()

        # Metadata file should exist
        assert store.metadata_file.exists()

    async def test_add_pair(self, store: MetadataStore, sample_pair: SyncPair) -> None:
        """Test adding sync pair."""
        await store.initialize()

        await store.add_pair(sample_pair)

        # Verify pair was added
        loaded_pair = await store.get_pair(sample_pair.id)
        assert loaded_pair is not None
        assert loaded_pair.id == sample_pair.id
        assert loaded_pair.local_path == sample_pair.local_path
        assert loaded_pair.remote_uri == sample_pair.remote_uri

    async def test_add_pair_updates_existing(
        self, store: MetadataStore, sample_pair: SyncPair
    ) -> None:
        """Test that adding a pair with same ID updates it."""
        await store.initialize()

        await store.add_pair(sample_pair)

        # Update pair
        sample_pair.local_path = "/new/path.md"
        await store.add_pair(sample_pair)

        # Verify updated
        loaded_pair = await store.get_pair(sample_pair.id)
        assert loaded_pair is not None
        assert loaded_pair.local_path == "/new/path.md"

    async def test_get_pair_nonexistent(self, store: MetadataStore) -> None:
        """Test getting nonexistent pair."""
        await store.initialize()

        pair = await store.get_pair("nonexistent")
        assert pair is None

    async def test_remove_pair(self, store: MetadataStore, sample_pair: SyncPair) -> None:
        """Test removing sync pair."""
        await store.initialize()

        await store.add_pair(sample_pair)
        assert await store.get_pair(sample_pair.id) is not None

        await store.remove_pair(sample_pair.id)
        assert await store.get_pair(sample_pair.id) is None

    async def test_remove_nonexistent_pair(self, store: MetadataStore) -> None:
        """Test removing nonexistent pair raises error."""
        await store.initialize()

        with pytest.raises(MetadataError, match="Pair not found"):
            await store.remove_pair("nonexistent")

    async def test_list_pairs(self, store: MetadataStore, sample_pair: SyncPair) -> None:
        """Test listing all pairs."""
        await store.initialize()

        # Initially empty
        pairs = await store.list_pairs()
        assert pairs == []

        # Add pair
        await store.add_pair(sample_pair)

        # Now has one pair
        pairs = await store.list_pairs()
        assert len(pairs) == 1
        assert pairs[0].id == sample_pair.id

        # Add another pair
        pair2 = SyncPair(
            id="pair-789",
            local_path="/other.md",
            remote_uri="notion://page-789",
            remote_platform="notion",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        await store.add_pair(pair2)

        # Now has two pairs
        pairs = await store.list_pairs()
        assert len(pairs) == 2

    async def test_update_pair_state(self, store: MetadataStore, sample_pair: SyncPair) -> None:
        """Test updating pair state."""
        await store.initialize()
        await store.add_pair(sample_pair)

        # Update state
        new_state = SyncPairState(
            local_hash="new123",
            remote_hash="new456",
            last_synced_hash="new123",
            last_sync=datetime(2024, 1, 3, 12, 0, 0),
            has_conflict=True,
            last_error="Some error",
        )
        await store.update_pair_state(sample_pair.id, new_state)

        # Verify state updated
        loaded_pair = await store.get_pair(sample_pair.id)
        assert loaded_pair is not None
        assert loaded_pair.state is not None
        assert loaded_pair.state.local_hash == "new123"
        assert loaded_pair.state.has_conflict is True
        assert loaded_pair.state.last_error == "Some error"

    async def test_update_nonexistent_pair_state(self, store: MetadataStore) -> None:
        """Test updating state of nonexistent pair raises error."""
        await store.initialize()

        state = SyncPairState(
            local_hash="abc",
            remote_hash="def",
            last_synced_hash="abc",
            last_sync=datetime.now(),
        )

        with pytest.raises(MetadataError, match="Pair not found"):
            await store.update_pair_state("nonexistent", state)

    async def test_get_config(self, store: MetadataStore) -> None:
        """Test getting configuration."""
        await store.initialize()

        # Get nonexistent key with default
        value = await store.get_config("missing", default="default")
        assert value == "default"

        # Set and get
        await store.set_config("key", "value")
        value = await store.get_config("key")
        assert value == "value"

    async def test_set_config(self, store: MetadataStore) -> None:
        """Test setting configuration."""
        await store.initialize()

        await store.set_config("string", "value")
        await store.set_config("number", 42)
        await store.set_config("bool", True)
        await store.set_config("dict", {"nested": "value"})

        # Verify all types work
        assert await store.get_config("string") == "value"
        assert await store.get_config("number") == 42
        assert await store.get_config("bool") is True
        assert await store.get_config("dict") == {"nested": "value"}

    async def test_exists(self, store: MetadataStore) -> None:
        """Test checking if metadata store exists."""
        # Initially doesn't exist
        assert store.exists() is False

        # After initialization, exists
        await store.initialize()
        assert store.exists() is True

    async def test_pair_serialization(self, store: MetadataStore, sample_pair: SyncPair) -> None:
        """Test that sync pairs are serialized and deserialized correctly."""
        await store.initialize()

        await store.add_pair(sample_pair)

        # Load directly from JSON to verify format
        data = await store.load()
        pair_dict = data["pairs"][sample_pair.id]

        # Verify all fields are present
        assert pair_dict["id"] == sample_pair.id
        assert pair_dict["local_path"] == sample_pair.local_path
        assert pair_dict["remote_uri"] == sample_pair.remote_uri
        assert pair_dict["remote_platform"] == sample_pair.remote_platform
        assert pair_dict["sync_direction"] == sample_pair.sync_direction.value
        assert pair_dict["conflict_resolution"] == sample_pair.conflict_resolution.value

        # Verify state is present
        assert pair_dict["state"] is not None
        assert sample_pair.state is not None  # For mypy
        assert pair_dict["state"]["local_hash"] == sample_pair.state.local_hash

    async def test_pair_without_state(self, store: MetadataStore) -> None:
        """Test handling pair without state."""
        await store.initialize()

        # Create pair without state
        pair = SyncPair(
            id="no-state",
            local_path="/path.md",
            remote_uri="notion://page",
            remote_platform="notion",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            state=None,
        )

        await store.add_pair(pair)

        # Load and verify
        loaded = await store.get_pair(pair.id)
        assert loaded is not None
        assert loaded.state is None

    async def test_invalid_json(self, store: MetadataStore) -> None:
        """Test handling invalid JSON in metadata file."""
        await store.initialize()

        # Write invalid JSON
        store.metadata_file.write_text("not valid json{")

        # Should raise MetadataError
        with pytest.raises(MetadataError, match="Invalid JSON"):
            await store.load()

    async def test_metadata_dir_path(self, tmp_path: Path) -> None:
        """Test that .portals is created in correct location."""
        store = MetadataStore(tmp_path)
        await store.initialize()

        assert store.metadata_dir == tmp_path / ".portals"
        assert store.metadata_file == tmp_path / ".portals" / "metadata.json"
