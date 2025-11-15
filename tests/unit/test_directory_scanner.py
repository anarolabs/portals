"""Tests for DirectoryScanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from portals.core.directory_scanner import DirectoryScanner


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """Create sample directory structure for testing."""
    # Create markdown files
    (tmp_path / "root.md").write_text("# Root file")
    (tmp_path / "another.md").write_text("# Another file")

    # Create subdirectory with files
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.md").write_text("# Nested file")
    (subdir / "doc.markdown").write_text("# Markdown file")

    # Create deeper nesting
    deepdir = subdir / "deep"
    deepdir.mkdir()
    (deepdir / "deep.md").write_text("# Deep file")

    # Create ignored directory
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    (gitdir / "config").write_text("git config")

    docsyncdir = tmp_path / ".portals"
    docsyncdir.mkdir()
    (docsyncdir / "metadata.json").write_text("{}")

    # Create non-markdown files
    (tmp_path / "readme.txt").write_text("Text file")
    (tmp_path / ".DS_Store").write_text("Mac file")

    return tmp_path


class TestDirectoryScanner:
    """Tests for DirectoryScanner."""

    def test_scan_finds_markdown_files(self, sample_dir: Path) -> None:
        """Test that scan finds markdown files."""
        scanner = DirectoryScanner(sample_dir)
        files = scanner.scan()

        # Should find all .md and .markdown files
        assert len(files) >= 5

        # Verify markdown files are found
        paths = {f.relative_path for f in files}
        assert Path("root.md") in paths
        assert Path("another.md") in paths
        assert Path("subdir/nested.md") in paths
        assert Path("subdir/doc.markdown") in paths
        assert Path("subdir/deep/deep.md") in paths

    def test_scan_ignores_git_directory(self, sample_dir: Path) -> None:
        """Test that .git directory is ignored."""
        scanner = DirectoryScanner(sample_dir, markdown_only=False)
        files = scanner.scan()

        # .git files should not be included
        paths = {f.relative_path for f in files}
        assert not any(".git" in str(p) for p in paths)

    def test_scan_ignores_docsync_directory(self, sample_dir: Path) -> None:
        """Test that .portals directory is ignored."""
        scanner = DirectoryScanner(sample_dir, markdown_only=False)
        files = scanner.scan()

        # .portals files should not be included
        paths = {f.relative_path for f in files}
        assert not any(".portals" in str(p) for p in paths)

    def test_scan_ignores_ds_store(self, sample_dir: Path) -> None:
        """Test that .DS_Store files are ignored."""
        scanner = DirectoryScanner(sample_dir, markdown_only=False)
        files = scanner.scan()

        # .DS_Store should not be included
        paths = {f.path.name for f in files}
        assert ".DS_Store" not in paths

    def test_scan_non_recursive(self, sample_dir: Path) -> None:
        """Test non-recursive scanning."""
        scanner = DirectoryScanner(sample_dir)
        files = scanner.scan(recursive=False)

        # Should only find root-level markdown files
        paths = {f.relative_path for f in files}
        assert Path("root.md") in paths
        assert Path("another.md") in paths

        # Should not find nested files
        assert Path("subdir/nested.md") not in paths
        assert Path("subdir/deep/deep.md") not in paths

    def test_scan_markdown_only(self, sample_dir: Path) -> None:
        """Test scanning for markdown files only."""
        scanner = DirectoryScanner(sample_dir, markdown_only=True)
        files = scanner.scan()

        # All files should be markdown
        for file in files:
            assert file.is_markdown
            assert file.path.suffix.lower() in scanner.MARKDOWN_EXTENSIONS

    def test_scan_all_files(self, sample_dir: Path) -> None:
        """Test scanning for all files."""
        scanner = DirectoryScanner(sample_dir, markdown_only=False)
        files = scanner.scan()

        # Should include non-markdown files
        paths = {f.relative_path for f in files}
        assert Path("readme.txt") in paths

        # Should still include markdown files
        assert Path("root.md") in paths

    def test_scan_markdown_convenience_method(self, sample_dir: Path) -> None:
        """Test scan_markdown convenience method."""
        scanner = DirectoryScanner(sample_dir, markdown_only=False)
        files = scanner.scan_markdown()

        # All files should be markdown
        for file in files:
            assert file.is_markdown

    def test_custom_ignore_dirs(self, sample_dir: Path) -> None:
        """Test custom ignore directories."""
        # Create custom directory to ignore
        customdir = sample_dir / "custom"
        customdir.mkdir()
        (customdir / "file.md").write_text("# Custom file")

        # Scan with custom ignore
        scanner = DirectoryScanner(sample_dir, ignore_dirs={"custom"})
        files = scanner.scan()

        # Custom directory should be ignored
        paths = {f.relative_path for f in files}
        assert Path("custom/file.md") not in paths

        # Normal files should still be found
        assert Path("root.md") in paths

    def test_custom_ignore_files(self, sample_dir: Path) -> None:
        """Test custom ignore files."""
        # Create custom file to ignore
        (sample_dir / "ignore_me.md").write_text("# Ignore me")

        # Scan with custom ignore
        scanner = DirectoryScanner(sample_dir, ignore_files={"ignore_me.md"})
        files = scanner.scan()

        # Custom file should be ignored
        paths = {f.relative_path for f in files}
        assert Path("ignore_me.md") not in paths

        # Normal files should still be found
        assert Path("root.md") in paths

    def test_count_files(self, sample_dir: Path) -> None:
        """Test counting files."""
        scanner = DirectoryScanner(sample_dir)

        # Count recursive
        count = scanner.count_files(recursive=True)
        assert count >= 5  # At least 5 markdown files

        # Count non-recursive
        count = scanner.count_files(recursive=False)
        assert count == 2  # Only root.md and another.md

    def test_get_file_tree(self, sample_dir: Path) -> None:
        """Test getting file tree."""
        scanner = DirectoryScanner(sample_dir)
        tree = scanner.get_file_tree()

        # Should have entries for each directory
        assert "." in tree  # Root directory
        assert "subdir" in tree
        assert str(Path("subdir/deep")) in tree

        # Root directory should have root files
        root_files = {f.path.name for f in tree["."]}
        assert "root.md" in root_files
        assert "another.md" in root_files

        # Subdir should have its files
        subdir_files = {f.path.name for f in tree["subdir"]}
        assert "nested.md" in subdir_files
        assert "doc.markdown" in subdir_files

    def test_file_info_attributes(self, sample_dir: Path) -> None:
        """Test FileInfo attributes."""
        scanner = DirectoryScanner(sample_dir)
        files = scanner.scan()

        for file in files:
            # All should have valid paths
            assert file.path.exists()
            assert file.path.is_file()

            # Relative path should be relative to base
            assert not file.relative_path.is_absolute()

            # Size should be positive
            assert file.size > 0

            # is_markdown should match extension
            if file.path.suffix.lower() in scanner.MARKDOWN_EXTENSIONS:
                assert file.is_markdown
            else:
                assert not file.is_markdown

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Test scanning nonexistent directory."""
        scanner = DirectoryScanner(tmp_path / "nonexistent")
        files = scanner.scan()

        # Should return empty list
        assert files == []

    def test_file_as_base_path(self, sample_dir: Path) -> None:
        """Test using a file as base path."""
        file_path = sample_dir / "root.md"
        scanner = DirectoryScanner(file_path)
        files = scanner.scan()

        # Should return empty list (not a directory)
        assert files == []

    def test_files_sorted_by_path(self, sample_dir: Path) -> None:
        """Test that files are sorted by relative path."""
        scanner = DirectoryScanner(sample_dir)
        files = scanner.scan()

        # Files should be sorted
        paths = [f.relative_path for f in files]
        assert paths == sorted(paths)

    def test_markdown_extensions(self, tmp_path: Path) -> None:
        """Test all markdown extensions are recognized."""
        # Create files with different markdown extensions
        (tmp_path / "test.md").write_text("# MD")
        (tmp_path / "test.markdown").write_text("# Markdown")
        (tmp_path / "test.mdown").write_text("# Mdown")
        (tmp_path / "test.mkd").write_text("# Mkd")
        (tmp_path / "test.mdwn").write_text("# Mdwn")

        scanner = DirectoryScanner(tmp_path)
        files = scanner.scan()

        # All should be found and marked as markdown
        assert len(files) == 5
        for file in files:
            assert file.is_markdown

    def test_case_insensitive_extensions(self, tmp_path: Path) -> None:
        """Test that markdown extensions are case-insensitive."""
        (tmp_path / "test.MD").write_text("# Uppercase MD")
        (tmp_path / "test.Md").write_text("# Mixed case Md")

        scanner = DirectoryScanner(tmp_path)
        files = scanner.scan()

        # Both should be found
        assert len(files) == 2
        for file in files:
            assert file.is_markdown

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Test scanning empty directory."""
        scanner = DirectoryScanner(tmp_path)
        files = scanner.scan()

        assert files == []

    def test_nested_ignored_directories(self, tmp_path: Path) -> None:
        """Test that nested ignored directories are properly filtered."""
        # Create nested .git directories
        gitdir = tmp_path / ".git"
        gitdir.mkdir()
        nested_git = gitdir / "subdir"
        nested_git.mkdir()
        (nested_git / "file.md").write_text("# Nested git file")

        scanner = DirectoryScanner(tmp_path)
        files = scanner.scan()

        # File in nested .git should not be found
        paths = {f.relative_path for f in files}
        assert Path(".git/subdir/file.md") not in paths
