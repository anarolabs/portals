"""Directory scanner for finding markdown files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileInfo:
    """Information about a scanned file."""

    path: Path
    relative_path: Path
    is_markdown: bool
    size: int


class DirectoryScanner:
    """Scans directories for markdown files.

    Recursively scans a directory tree and returns information about
    markdown files, while filtering out ignored paths.
    """

    DEFAULT_IGNORE_DIRS = {
        ".docsync",
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }

    DEFAULT_IGNORE_FILES = {
        ".DS_Store",
        "Thumbs.db",
        ".gitignore",
        ".gitattributes",
    }

    MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd", ".mdwn"}

    def __init__(
        self,
        base_path: str | Path,
        ignore_dirs: set[str] | None = None,
        ignore_files: set[str] | None = None,
        markdown_only: bool = True,
    ) -> None:
        """Initialize directory scanner.

        Args:
            base_path: Base directory to scan
            ignore_dirs: Set of directory names to ignore (adds to defaults)
            ignore_files: Set of file names to ignore (adds to defaults)
            markdown_only: If True, only return markdown files
        """
        self.base_path = Path(base_path).resolve()
        self.markdown_only = markdown_only

        # Combine default and custom ignore lists
        self.ignore_dirs = self.DEFAULT_IGNORE_DIRS.copy()
        if ignore_dirs:
            self.ignore_dirs.update(ignore_dirs)

        self.ignore_files = self.DEFAULT_IGNORE_FILES.copy()
        if ignore_files:
            self.ignore_files.update(ignore_files)

    def scan(self, recursive: bool = True) -> list[FileInfo]:
        """Scan directory for files.

        Args:
            recursive: If True, scan subdirectories recursively

        Returns:
            List of FileInfo objects for found files
        """
        files: list[FileInfo] = []

        if not self.base_path.exists():
            return files

        if not self.base_path.is_dir():
            return files

        # Use rglob for recursive, iterdir for non-recursive
        if recursive:
            iterator = self.base_path.rglob("*")
        else:
            iterator = self.base_path.iterdir()

        for path in iterator:
            # Skip if not a file
            if not path.is_file():
                continue

            # Skip if in ignored directory
            if self._is_in_ignored_dir(path):
                continue

            # Skip if ignored file
            if path.name in self.ignore_files:
                continue

            # Check if markdown
            is_markdown = path.suffix.lower() in self.MARKDOWN_EXTENSIONS

            # Skip if not markdown and markdown_only is True
            if self.markdown_only and not is_markdown:
                continue

            # Get relative path
            try:
                relative_path = path.relative_to(self.base_path)
            except ValueError:
                # Path is not relative to base_path (shouldn't happen)
                continue

            # Get file size
            size = path.stat().st_size

            files.append(
                FileInfo(
                    path=path,
                    relative_path=relative_path,
                    is_markdown=is_markdown,
                    size=size,
                )
            )

        return sorted(files, key=lambda f: f.relative_path)

    def scan_markdown(self) -> list[FileInfo]:
        """Scan directory for markdown files only.

        Convenience method that ensures only markdown files are returned.

        Returns:
            List of FileInfo objects for markdown files
        """
        self.markdown_only = True
        return self.scan(recursive=True)

    def count_files(self, recursive: bool = True) -> int:
        """Count files in directory.

        Args:
            recursive: If True, count in subdirectories

        Returns:
            Number of files found
        """
        return len(self.scan(recursive=recursive))

    def get_file_tree(self) -> dict[str, list[FileInfo]]:
        """Get files organized by directory.

        Returns:
            Dictionary mapping directory paths to lists of files in that directory
        """
        tree: dict[str, list[FileInfo]] = {}

        for file_info in self.scan():
            dir_path = str(file_info.relative_path.parent)

            if dir_path not in tree:
                tree[dir_path] = []

            tree[dir_path].append(file_info)

        return tree

    def _is_in_ignored_dir(self, path: Path) -> bool:
        """Check if path is in an ignored directory.

        Args:
            path: Path to check

        Returns:
            True if path is in ignored directory
        """
        try:
            relative = path.relative_to(self.base_path)

            # Check each parent directory
            for parent in relative.parents:
                if parent.name in self.ignore_dirs:
                    return True

            return False

        except ValueError:
            # Path is not relative to base_path
            return False
