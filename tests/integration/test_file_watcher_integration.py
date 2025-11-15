"""Integration tests for FileWatcher with real file system."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from portals.watcher.file_watcher import FileWatcher, ChangeEvent


@pytest.fixture
def test_dir(tmp_path):
    """Create a temporary test directory."""
    return tmp_path


@pytest.fixture
def change_queue():
    """Queue to collect change events."""
    return []


@pytest.fixture
def callback(change_queue):
    """Callback that appends to queue."""
    def _callback(event: ChangeEvent):
        change_queue.append(event)
    return _callback


class TestFileWatcherIntegration:
    """Integration tests for FileWatcher with real filesystem."""

    def test_detects_new_file_creation(self, test_dir, callback, change_queue):
        """Test that FileWatcher detects new file creation."""
        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=0.5,
        )

        try:
            watcher.start()
            time.sleep(0.5)  # Give watcher time to start

            # Create a new markdown file
            test_file = test_dir / "new_file.md"
            test_file.write_text("# New File\n\nContent here")

            # Wait for event processing (debounce + processing time)
            time.sleep(1.5)

            # Should have detected the creation
            assert len(change_queue) >= 1
            event = change_queue[0]
            assert event.path == Path("new_file.md")
            assert event.event_type in ["created", "modified"]

        finally:
            watcher.stop()

    def test_detects_file_modification(self, test_dir, callback, change_queue):
        """Test that FileWatcher detects file modifications."""
        # Create existing file
        test_file = test_dir / "existing.md"
        test_file.write_text("# Original Content")

        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=0.5,
        )

        try:
            watcher.start()
            time.sleep(0.5)  # Give watcher time to start

            # Modify the file
            test_file.write_text("# Modified Content\n\nNew content added")

            # Wait for event
            time.sleep(1.5)

            # Should have detected modification
            assert len(change_queue) >= 1
            event = change_queue[-1]  # Get last event
            assert event.path == Path("existing.md")
            assert event.event_type == "modified"

        finally:
            watcher.stop()

    def test_ignores_non_md_files(self, test_dir, callback, change_queue):
        """Test that non-.md files are ignored."""
        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=0.5,
        )

        try:
            watcher.start()
            time.sleep(0.5)

            # Create non-.md files
            (test_dir / "test.txt").write_text("text file")
            (test_dir / "test.py").write_text("python file")
            (test_dir / "README").write_text("readme file")

            # Wait
            time.sleep(1.5)

            # Should not have detected any changes
            assert len(change_queue) == 0

        finally:
            watcher.stop()

    def test_ignores_docsync_directory(self, test_dir, callback, change_queue):
        """Test that .portals directory is ignored."""
        # Create .portals directory
        docsync_dir = test_dir / ".portals"
        docsync_dir.mkdir()

        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=0.5,
        )

        try:
            watcher.start()
            time.sleep(0.5)

            # Create file in .portals
            (docsync_dir / "metadata.json").write_text('{"test": true}')

            # Wait
            time.sleep(1.5)

            # Should not have detected any changes
            assert len(change_queue) == 0

        finally:
            watcher.stop()

    def test_detects_nested_file_changes(self, test_dir, callback, change_queue):
        """Test detection of changes in nested directories."""
        # Create nested directory
        nested = test_dir / "project" / "docs"
        nested.mkdir(parents=True)

        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=0.5,
        )

        try:
            watcher.start()
            time.sleep(0.5)

            # Create file in nested directory
            test_file = nested / "nested.md"
            test_file.write_text("# Nested Document")

            # Wait
            time.sleep(1.5)

            # Should have detected the change
            assert len(change_queue) >= 1
            event = change_queue[0]
            assert event.path == Path("project/docs/nested.md")

        finally:
            watcher.stop()

    def test_debouncing_rapid_changes(self, test_dir, callback, change_queue):
        """Test that rapid changes are debounced."""
        test_file = test_dir / "rapid.md"
        test_file.write_text("# Initial")

        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=1.0,  # Longer debounce for this test
        )

        try:
            watcher.start()
            time.sleep(0.5)

            # Make rapid changes
            for i in range(5):
                test_file.write_text(f"# Version {i}")
                time.sleep(0.2)  # Less than debounce time

            # Wait for debounce to settle
            time.sleep(2.0)

            # Should have triggered fewer events than modifications
            # (exact count depends on timing, but should be < 5)
            assert len(change_queue) < 5
            assert len(change_queue) >= 1

        finally:
            watcher.stop()

    def test_file_deletion_detection(self, test_dir, callback, change_queue):
        """Test that file deletion is detected."""
        # Create file
        test_file = test_dir / "to_delete.md"
        test_file.write_text("# Will be deleted")

        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=0.5,
        )

        try:
            watcher.start()
            time.sleep(0.5)

            # Delete file
            test_file.unlink()

            # Wait
            time.sleep(1.0)

            # Should have detected deletion
            assert len(change_queue) >= 1
            event = change_queue[-1]
            assert event.path == Path("to_delete.md")
            assert event.event_type == "deleted"

        finally:
            watcher.stop()

    def test_multiple_files_simultaneously(self, test_dir, callback, change_queue):
        """Test handling multiple file changes simultaneously."""
        watcher = FileWatcher(
            base_path=test_dir,
            on_change_callback=callback,
            debounce_seconds=0.5,
        )

        try:
            watcher.start()
            time.sleep(0.5)

            # Create multiple files at once
            for i in range(3):
                (test_dir / f"file{i}.md").write_text(f"# File {i}")

            # Wait
            time.sleep(1.5)

            # Should have detected all files
            assert len(change_queue) >= 3

        finally:
            watcher.stop()
