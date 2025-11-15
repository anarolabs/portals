"""Tests for FileWatcher."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from watchdog.events import FileModifiedEvent, FileCreatedEvent, FileDeletedEvent

from portals.watcher.file_watcher import (
    ChangeEvent,
    FileWatcher,
    FileWatcherHandler,
)


@pytest.fixture
def mock_callback():
    """Mock callback function."""
    return Mock()


@pytest.fixture
def base_path(tmp_path):
    """Create test base path."""
    return tmp_path


@pytest.fixture
def handler(base_path, mock_callback):
    """Create FileWatcherHandler instance."""
    return FileWatcherHandler(
        base_path=base_path,
        on_change_callback=mock_callback,
        debounce_seconds=0.1,  # Short debounce for testing
    )


class TestFileWatcherHandler:
    """Tests for FileWatcherHandler."""

    def test_should_process_md_file(self, handler, base_path):
        """Test that .md files are processed."""
        event = FileModifiedEvent(str(base_path / "test.md"))
        assert handler._should_process(event) is True

    def test_should_ignore_non_md_files(self, handler, base_path):
        """Test that non-.md files are ignored."""
        event = FileModifiedEvent(str(base_path / "test.txt"))
        assert handler._should_process(event) is False

        event = FileModifiedEvent(str(base_path / "test.py"))
        assert handler._should_process(event) is False

    def test_should_ignore_directories(self, handler, base_path):
        """Test that directories are ignored."""
        event = Mock(src_path=str(base_path / "subdir"), is_directory=True)
        assert handler._should_process(event) is False

    def test_should_ignore_hidden_files(self, handler, base_path):
        """Test that hidden files are ignored."""
        event = FileModifiedEvent(str(base_path / ".hidden.md"))
        assert handler._should_process(event) is False

    def test_should_ignore_docsync_directory(self, handler, base_path):
        """Test that .portals directory is ignored."""
        event = FileModifiedEvent(str(base_path / ".portals" / "metadata.json"))
        assert handler._should_process(event) is False

    def test_should_ignore_git_directory(self, handler, base_path):
        """Test that .git directory is ignored."""
        event = FileModifiedEvent(str(base_path / ".git" / "config"))
        assert handler._should_process(event) is False

    def test_should_process_nested_md_files(self, handler, base_path):
        """Test that nested .md files are processed."""
        event = FileModifiedEvent(str(base_path / "project" / "notes.md"))
        assert handler._should_process(event) is True

    def test_on_created_triggers_callback(self, handler, mock_callback, base_path):
        """Test that file created event triggers callback."""
        test_file = base_path / "new.md"
        test_file.touch()

        event = FileCreatedEvent(str(test_file))
        handler.on_created(event)

        # Wait for debounce
        time.sleep(0.2)

        # Callback should be called
        assert mock_callback.call_count == 1
        change_event = mock_callback.call_args[0][0]
        assert isinstance(change_event, ChangeEvent)
        assert change_event.path == Path("new.md")
        assert change_event.event_type == "created"

    def test_on_modified_triggers_callback(self, handler, mock_callback, base_path):
        """Test that file modified event triggers callback."""
        test_file = base_path / "existing.md"
        test_file.write_text("content")

        event = FileModifiedEvent(str(test_file))
        handler.on_modified(event)

        # Wait for debounce
        time.sleep(0.2)

        # Callback should be called
        assert mock_callback.call_count == 1
        change_event = mock_callback.call_args[0][0]
        assert change_event.event_type == "modified"

    def test_on_deleted_triggers_callback_immediately(
        self, handler, mock_callback, base_path
    ):
        """Test that file deleted event triggers callback without debounce."""
        test_file = base_path / "deleted.md"

        event = FileDeletedEvent(str(test_file))
        handler.on_deleted(event)

        # No need to wait - deletes aren't debounced
        assert mock_callback.call_count == 1
        change_event = mock_callback.call_args[0][0]
        assert change_event.event_type == "deleted"

    def test_debouncing_multiple_changes(self, handler, mock_callback, base_path):
        """Test that multiple rapid changes are debounced."""
        test_file = base_path / "rapid.md"
        test_file.write_text("content")

        event = FileModifiedEvent(str(test_file))

        # Trigger multiple events rapidly (all within debounce window)
        handler.on_modified(event)
        time.sleep(0.03)
        handler.on_modified(event)
        time.sleep(0.03)
        handler.on_modified(event)

        # Wait for debounce period
        time.sleep(0.12)

        # First event should fire after initial debounce period
        # Rapid subsequent events should be debounced
        assert mock_callback.call_count >= 1  # At least one callback
        assert mock_callback.call_count <= 2  # But not more than 2

    def test_ignores_non_md_files(self, handler, mock_callback, base_path):
        """Test that non-.md files don't trigger callback."""
        test_file = base_path / "test.txt"
        test_file.write_text("content")

        event = FileModifiedEvent(str(test_file))
        handler.on_modified(event)

        time.sleep(0.2)

        # Callback should not be called
        assert mock_callback.call_count == 0


class TestFileWatcher:
    """Tests for FileWatcher."""

    def test_initialization(self, base_path, mock_callback):
        """Test FileWatcher initialization."""
        watcher = FileWatcher(
            base_path=base_path,
            on_change_callback=mock_callback,
            debounce_seconds=2.0,
        )

        assert watcher.base_path == base_path
        assert watcher.on_change_callback == mock_callback
        assert watcher.debounce_seconds == 2.0
        assert watcher.is_running is False

    def test_start_and_stop(self, base_path, mock_callback):
        """Test starting and stopping watcher."""
        watcher = FileWatcher(
            base_path=base_path,
            on_change_callback=mock_callback,
        )

        # Start
        watcher.start()
        assert watcher.is_running is True
        assert watcher.observer is not None
        assert watcher.handler is not None

        # Stop
        watcher.stop()
        assert watcher.is_running is False

    def test_context_manager(self, base_path, mock_callback):
        """Test FileWatcher as context manager."""
        with FileWatcher(
            base_path=base_path,
            on_change_callback=mock_callback,
        ) as watcher:
            assert watcher.is_running is True

        # Should be stopped after context
        assert watcher.is_running is False

    def test_double_start_warning(self, base_path, mock_callback):
        """Test that double start is handled gracefully."""
        watcher = FileWatcher(
            base_path=base_path,
            on_change_callback=mock_callback,
        )

        watcher.start()
        watcher.start()  # Should log warning but not crash

        assert watcher.is_running is True

        watcher.stop()

    def test_stop_when_not_running(self, base_path, mock_callback):
        """Test that stop when not running is handled gracefully."""
        watcher = FileWatcher(
            base_path=base_path,
            on_change_callback=mock_callback,
        )

        watcher.stop()  # Should log warning but not crash


class TestChangeEvent:
    """Tests for ChangeEvent."""

    def test_change_event_creation(self):
        """Test ChangeEvent creation."""
        path = Path("test.md")
        timestamp = time.time()

        event = ChangeEvent(
            path=path,
            event_type="modified",
            timestamp=timestamp,
        )

        assert event.path == path
        assert event.event_type == "modified"
        assert event.timestamp == timestamp

    def test_change_event_repr(self):
        """Test ChangeEvent string representation."""
        path = Path("test.md")
        timestamp = time.time()

        event = ChangeEvent(
            path=path,
            event_type="created",
            timestamp=timestamp,
        )

        repr_str = repr(event)
        assert "ChangeEvent" in repr_str
        assert "test.md" in repr_str
        assert "created" in repr_str
