#!/usr/bin/env python3
"""
Google Drive SQLite index - queryable index of all Google Drive files across both accounts.

Indexes files from Google Drive (via API crawl) and local markdown references,
stored in a SQLite database for fast lookup by Claude and scripts.

Usage:
    # Seed from local markdown files
    python3 drive_index.py seed --project anaro-labs
    python3 drive_index.py seed --project estate-mate

    # Crawl Drive recursively
    python3 drive_index.py crawl --project anaro-labs
    python3 drive_index.py crawl --project estate-mate

    # Incremental refresh
    python3 drive_index.py refresh --project anaro-labs
    python3 drive_index.py refresh --id FILE_ID --project anaro-labs

    # Search by name/keyword
    python3 drive_index.py search "capacity plan"
    python3 drive_index.py search "pricing" --project estate-mate

    # Lookup by ID or name
    python3 drive_index.py lookup --id FILE_ID
    python3 drive_index.py lookup --name "SOW"

    # Filtered list
    python3 drive_index.py list --project anaro-labs --mime spreadsheet
    python3 drive_index.py list --tag deliverable --folder "GYG"

    # Show local file references for a Google Doc
    python3 drive_index.py refs --id FILE_ID

    # Stats summary
    python3 drive_index.py stats

    # Tag management
    python3 drive_index.py tag --id FILE_ID --tags deliverable,gyg
    python3 drive_index.py untag --id FILE_ID --tags gyg
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from google_client import get_drive_service, _resolve_project

# Optional Athena tracing
try:
    import sys as _sys
    _sys.path.insert(0, "/Users/romansiepelmeyer/Documents/Claude Code/athena")
    from spans import start_span, end_span
    _HAS_ATHENA = True
except Exception:
    _HAS_ATHENA = False

# =============================================================================
# Constants
# =============================================================================

DB_PATH = Path(__file__).parent.parent / "drive-index.db"

# Project root directories for seeding
PROJECT_ROOTS = {
    "anaro-labs": Path.home() / "Documents/Claude Code/anaro-labs",
    "estate-mate": Path.home() / "Documents/Claude Code/estate-mate",
}

# Google URL patterns
GOOGLE_URL_PATTERNS = [
    # docs.google.com/document/d/ID
    re.compile(r'https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)'),
    # docs.google.com/spreadsheets/d/ID
    re.compile(r'https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)'),
    # docs.google.com/presentation/d/ID
    re.compile(r'https?://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)'),
    # docs.google.com/forms/d/ID
    re.compile(r'https?://docs\.google\.com/forms/d/([a-zA-Z0-9_-]+)'),
    # drive.google.com/file/d/ID
    re.compile(r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)'),
    # drive.google.com/open?id=ID
    re.compile(r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)'),
    # drive.google.com/drive/folders/ID
    re.compile(r'https?://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)'),
]

# Full URL extraction (for storing the URL)
FULL_URL_PATTERN = re.compile(
    r'(https?://(?:docs|drive)\.google\.com/[^\s\)>\]"\']+)'
)

# MIME type shortcuts for --mime filter
MIME_SHORTCUTS = {
    "doc": "application/vnd.google-apps.document",
    "document": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
    "slides": "application/vnd.google-apps.presentation",
    "presentation": "application/vnd.google-apps.presentation",
    "folder": "application/vnd.google-apps.folder",
    "pdf": "application/pdf",
    "form": "application/vnd.google-apps.form",
}

# Drive API fields for file listing
FILE_FIELDS = (
    "id, name, mimeType, webViewLink, parents, createdTime, modifiedTime, "
    "size, description, owners"
)

# =============================================================================
# Database
# =============================================================================


def get_db() -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    _init_db(db)
    return db


def _init_db(db: sqlite3.Connection):
    """Create tables if they don't exist."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            file_id TEXT NOT NULL,
            project TEXT NOT NULL,
            name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            url TEXT,
            parent_folder_id TEXT,
            parent_folder_name TEXT,
            path TEXT,
            created_at TEXT,
            modified_at TEXT,
            size INTEGER,
            description TEXT,
            owner_email TEXT,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (file_id, project)
        );

        CREATE TABLE IF NOT EXISTS folders (
            folder_id TEXT NOT NULL,
            project TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_folder_id TEXT,
            path TEXT,
            crawled_at TEXT,
            PRIMARY KEY (folder_id, project)
        );

        CREATE TABLE IF NOT EXISTS local_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            project TEXT NOT NULL,
            local_file_path TEXT NOT NULL,
            context TEXT,
            discovered_at TEXT NOT NULL,
            UNIQUE(file_id, project, local_file_path)
        );

        CREATE TABLE IF NOT EXISTS file_tags (
            file_id TEXT NOT NULL,
            project TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (file_id, project, tag)
        );

        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            operation TEXT NOT NULL,
            files_discovered INTEGER DEFAULT 0,
            files_updated INTEGER DEFAULT 0,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT DEFAULT 'running',
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
        CREATE INDEX IF NOT EXISTS idx_files_project ON files(project);
        CREATE INDEX IF NOT EXISTS idx_files_mime ON files(mime_type);
        CREATE INDEX IF NOT EXISTS idx_files_modified ON files(modified_at);
        CREATE INDEX IF NOT EXISTS idx_folders_project ON folders(project);
        CREATE INDEX IF NOT EXISTS idx_local_refs_file ON local_references(file_id);
        CREATE INDEX IF NOT EXISTS idx_tags_tag ON file_tags(tag);
    """)
    db.commit()


def _now() -> str:
    """ISO timestamp for now (UTC)."""
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Seed command - parse markdown files for Google URLs
# =============================================================================


def _extract_context(line: str, file_id: str) -> str | None:
    """Extract context label near a Google URL in a line."""
    # Table format: | Label | `ID` | URL |
    # Try to get the first cell content
    if "|" in line:
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if cells:
            # Return the first cell that isn't the ID or URL
            for cell in cells:
                if file_id not in cell and "google.com" not in cell:
                    # Strip markdown formatting
                    clean = re.sub(r'[`*\[\]]', '', cell).strip()
                    if clean and len(clean) < 200:
                        return clean
    # Markdown link: [label](url)
    link_match = re.search(r'\[([^\]]+)\]\([^)]*' + re.escape(file_id) + r'[^)]*\)', line)
    if link_match:
        return link_match.group(1).strip()
    # Key-value: **Label**: url or Label: url
    kv_match = re.search(r'\*\*([^*]+)\*\*\s*[:：]', line)
    if kv_match:
        return kv_match.group(1).strip()
    kv_match2 = re.search(r'^[-*]\s*(.+?):\s*https?://', line)
    if kv_match2:
        label = kv_match2.group(1).strip()
        label = re.sub(r'[`*\[\]]', '', label).strip()
        if label and len(label) < 200:
            return label
    return None


def _infer_mime_type(url: str) -> str:
    """Infer MIME type from Google URL pattern."""
    if "/document/" in url:
        return "application/vnd.google-apps.document"
    elif "/spreadsheets/" in url:
        return "application/vnd.google-apps.spreadsheet"
    elif "/presentation/" in url:
        return "application/vnd.google-apps.presentation"
    elif "/forms/" in url:
        return "application/vnd.google-apps.form"
    elif "/drive/folders/" in url:
        return "application/vnd.google-apps.folder"
    return "unknown"


def cmd_seed(project: str):
    """Parse markdown files for Google URLs, populate files + local_references."""
    root = PROJECT_ROOTS.get(project)
    if not root or not root.exists():
        print(json.dumps({"error": f"Project root not found: {root}"}), file=sys.stderr)
        sys.exit(1)

    db = get_db()
    now = _now()
    log_id = _start_crawl_log(db, project, "seed")

    files_discovered = 0
    refs_added = 0

    # Walk all .md files in the project
    for md_file in sorted(root.rglob("*.md")):
        # Skip node_modules, .git, etc.
        parts = md_file.parts
        if any(p.startswith(".") or p == "node_modules" or p == "__pycache__" for p in parts):
            continue

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        lines = content.split("\n")
        seen_ids_in_file = set()

        for line in lines:
            # Find all Google URLs in this line
            urls = FULL_URL_PATTERN.findall(line)
            for url in urls:
                # Extract file ID
                file_id = None
                for pattern in GOOGLE_URL_PATTERNS:
                    m = pattern.search(url)
                    if m:
                        file_id = m.group(1)
                        break
                if not file_id:
                    continue

                # Skip duplicate IDs within same file
                if file_id in seen_ids_in_file:
                    continue
                seen_ids_in_file.add(file_id)

                # Clean URL (remove trailing punctuation)
                clean_url = url.rstrip(".,;:)>]\"'")

                mime_type = _infer_mime_type(clean_url)
                context = _extract_context(line, file_id)

                # Determine a name - use context or "Unknown"
                name = context or "Unknown"

                # Insert/update file (seed creates minimal records)
                is_folder = mime_type == "application/vnd.google-apps.folder"
                if is_folder:
                    db.execute("""
                        INSERT INTO folders (folder_id, project, name, path)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(folder_id, project) DO UPDATE SET
                            name = CASE WHEN excluded.name != 'Unknown' THEN excluded.name ELSE folders.name END
                    """, (file_id, project, name, None))
                else:
                    db.execute("""
                        INSERT INTO files (file_id, project, name, mime_type, url, indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(file_id, project) DO UPDATE SET
                            url = COALESCE(excluded.url, files.url),
                            name = CASE WHEN excluded.name != 'Unknown' THEN excluded.name ELSE files.name END,
                            mime_type = CASE WHEN files.mime_type = 'unknown' THEN excluded.mime_type ELSE files.mime_type END
                    """, (file_id, project, name, mime_type, clean_url, now))
                    files_discovered += 1

                # Insert local reference
                local_path = str(md_file)
                db.execute("""
                    INSERT INTO local_references (file_id, project, local_file_path, context, discovered_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(file_id, project, local_file_path) DO UPDATE SET
                        context = COALESCE(excluded.context, local_references.context)
                """, (file_id, project, local_path, context, now))
                refs_added += 1

    db.commit()
    _finish_crawl_log(db, log_id, files_discovered, 0)

    print(json.dumps({
        "operation": "seed",
        "project": project,
        "files_discovered": files_discovered,
        "references_added": refs_added,
    }, indent=2))


# =============================================================================
# Crawl command - recursive BFS crawl of Drive
# =============================================================================


def cmd_crawl(project: str, folder_id: str = None, max_depth: int = 10):
    """BFS crawl of Google Drive, populating files + folders."""
    service = get_drive_service(project=project)
    db = get_db()
    now = _now()
    log_id = _start_crawl_log(db, project, "crawl")

    files_discovered = 0
    files_updated = 0

    # BFS queue: (folder_id, depth, path_parts)
    start_id = folder_id or "root"
    start_path = "My Drive" if start_id == "root" else start_id
    queue = deque([(start_id, 0, [start_path])])

    try:
        while queue:
            current_id, depth, path_parts = queue.popleft()

            if depth > max_depth:
                continue

            current_path = "/".join(path_parts)
            page_token = None

            while True:
                # Query folder contents
                q = f"'{current_id}' in parents and trashed = false"
                try:
                    result = service.files().list(
                        q=q,
                        pageSize=100,
                        fields=f"nextPageToken, files({FILE_FIELDS})",
                        pageToken=page_token,
                    ).execute()
                except Exception as e:
                    print(f"  Warning: Error listing {current_id}: {e}", file=sys.stderr)
                    break

                items = result.get("files", [])

                for item in items:
                    item_id = item["id"]
                    item_name = item["name"]
                    item_mime = item["mimeType"]
                    is_folder = item_mime == "application/vnd.google-apps.folder"

                    owner_email = None
                    owners = item.get("owners", [])
                    if owners:
                        owner_email = owners[0].get("emailAddress")

                    if is_folder:
                        folder_path = current_path + "/" + item_name
                        db.execute("""
                            INSERT INTO folders (folder_id, project, name, parent_folder_id, path, crawled_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(folder_id, project) DO UPDATE SET
                                name = excluded.name,
                                parent_folder_id = excluded.parent_folder_id,
                                path = excluded.path,
                                crawled_at = excluded.crawled_at
                        """, (item_id, project, item_name, current_id if current_id != "root" else None,
                              folder_path, now))

                        # Enqueue for further crawling
                        queue.append((item_id, depth + 1, path_parts + [item_name]))
                    else:
                        # Get parent folder name from path
                        parent_name = path_parts[-1] if path_parts else None
                        file_path = current_path + "/" + item_name

                        # Check if already exists
                        existing = db.execute(
                            "SELECT file_id FROM files WHERE file_id = ? AND project = ?",
                            (item_id, project)
                        ).fetchone()

                        db.execute("""
                            INSERT INTO files (
                                file_id, project, name, mime_type, url,
                                parent_folder_id, parent_folder_name, path,
                                created_at, modified_at, size, description,
                                owner_email, indexed_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(file_id, project) DO UPDATE SET
                                name = excluded.name,
                                mime_type = excluded.mime_type,
                                url = COALESCE(excluded.url, files.url),
                                parent_folder_id = excluded.parent_folder_id,
                                parent_folder_name = excluded.parent_folder_name,
                                path = excluded.path,
                                created_at = excluded.created_at,
                                modified_at = excluded.modified_at,
                                size = excluded.size,
                                description = excluded.description,
                                owner_email = excluded.owner_email,
                                indexed_at = excluded.indexed_at
                        """, (
                            item_id, project, item_name, item_mime,
                            item.get("webViewLink"),
                            current_id if current_id != "root" else None,
                            parent_name, file_path,
                            item.get("createdTime"), item.get("modifiedTime"),
                            item.get("size"), item.get("description"),
                            owner_email, now,
                        ))

                        if existing:
                            files_updated += 1
                        else:
                            files_discovered += 1

                # Commit per page
                db.commit()

                page_token = result.get("nextPageToken")
                if not page_token:
                    break

                # Rate limit safety
                time.sleep(0.1)

            # Record folder as crawled
            if current_id != "root":
                db.execute("""
                    UPDATE folders SET crawled_at = ? WHERE folder_id = ? AND project = ?
                """, (now, current_id, project))
                db.commit()

    except Exception as e:
        _finish_crawl_log(db, log_id, files_discovered, files_updated, error=str(e))
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    _finish_crawl_log(db, log_id, files_discovered, files_updated)

    print(json.dumps({
        "operation": "crawl",
        "project": project,
        "files_discovered": files_discovered,
        "files_updated": files_updated,
        "folder_start": folder_id or "root",
        "max_depth": max_depth,
    }, indent=2))


# =============================================================================
# Refresh command - incremental update
# =============================================================================


def cmd_refresh(project: str, file_id: str = None):
    """Refresh metadata for recently modified files or a specific file."""
    # Start Athena span (best-effort)
    span_id = None
    try:
        if _HAS_ATHENA:
            trace_id = os.environ.get("TRACE_ID")
            span_id = start_span("refresh:drive-index", kind="scan",
                                 parent=trace_id, project=project)
    except Exception:
        pass

    service = get_drive_service(project=project)
    db = get_db()
    now = _now()
    log_id = _start_crawl_log(db, project, "refresh")

    files_updated = 0
    files_total = 0
    refresh_error = None

    if file_id:
        # Single file refresh
        files_total = 1
        try:
            item = service.files().get(
                fileId=file_id,
                fields=FILE_FIELDS
            ).execute()
            _upsert_file_from_api(db, project, item, now)
            files_updated = 1
            db.commit()
        except Exception as e:
            refresh_error = str(e)
            _finish_crawl_log(db, log_id, 0, 0, error=str(e))
            output = json.dumps({"error": str(e)})
            print(output, file=sys.stderr)
            # End span on error
            try:
                if span_id:
                    end_span(span_id, status="error",
                             metrics={"files_new": 0, "files_updated": 0, "files_total": 1},
                             error=str(e), raw_output=output)
            except Exception:
                pass
            sys.exit(1)
    else:
        # Batch refresh: get all indexed file IDs and re-fetch
        rows = db.execute(
            "SELECT file_id FROM files WHERE project = ? ORDER BY modified_at DESC LIMIT 200",
            (project,)
        ).fetchall()
        files_total = len(rows)

        for row in rows:
            try:
                item = service.files().get(
                    fileId=row["file_id"],
                    fields=FILE_FIELDS
                ).execute()
                _upsert_file_from_api(db, project, item, now)
                files_updated += 1
            except Exception:
                # File may have been deleted or access revoked
                pass

            if files_updated % 50 == 0:
                db.commit()
                time.sleep(0.1)

        db.commit()

    _finish_crawl_log(db, log_id, 0, files_updated)

    output = json.dumps({
        "operation": "refresh",
        "project": project,
        "files_updated": files_updated,
        "single_file": file_id,
    }, indent=2)

    # End Athena span with metrics and raw output (best-effort)
    try:
        if span_id:
            end_span(span_id, status="ok",
                     metrics={"files_new": 0, "files_updated": files_updated,
                              "files_total": files_total},
                     raw_output=output)
    except Exception:
        pass

    print(output)


def _upsert_file_from_api(db: sqlite3.Connection, project: str, item: dict, now: str):
    """Insert or update a file record from Drive API response."""
    owner_email = None
    owners = item.get("owners", [])
    if owners:
        owner_email = owners[0].get("emailAddress")

    parents = item.get("parents", [])
    parent_id = parents[0] if parents else None

    db.execute("""
        INSERT INTO files (
            file_id, project, name, mime_type, url,
            parent_folder_id, created_at, modified_at,
            size, description, owner_email, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id, project) DO UPDATE SET
            name = excluded.name,
            mime_type = excluded.mime_type,
            url = COALESCE(excluded.url, files.url),
            parent_folder_id = excluded.parent_folder_id,
            created_at = excluded.created_at,
            modified_at = excluded.modified_at,
            size = excluded.size,
            description = excluded.description,
            owner_email = excluded.owner_email,
            indexed_at = excluded.indexed_at
    """, (
        item["id"], project, item["name"], item["mimeType"],
        item.get("webViewLink"), parent_id,
        item.get("createdTime"), item.get("modifiedTime"),
        item.get("size"), item.get("description"),
        owner_email, now,
    ))


# =============================================================================
# Query commands
# =============================================================================


def cmd_search(query: str, project: str = None):
    """Search files by name or description."""
    db = get_db()
    search_term = f"%{query}%"

    if project:
        rows = db.execute("""
            SELECT f.*, GROUP_CONCAT(t.tag) as tags
            FROM files f
            LEFT JOIN file_tags t ON f.file_id = t.file_id AND f.project = t.project
            WHERE f.project = ? AND (f.name LIKE ? OR f.description LIKE ? OR f.path LIKE ?)
            GROUP BY f.file_id, f.project
            ORDER BY f.modified_at DESC
        """, (project, search_term, search_term, search_term)).fetchall()
    else:
        rows = db.execute("""
            SELECT f.*, GROUP_CONCAT(t.tag) as tags
            FROM files f
            LEFT JOIN file_tags t ON f.file_id = t.file_id AND f.project = t.project
            WHERE f.name LIKE ? OR f.description LIKE ? OR f.path LIKE ?
            GROUP BY f.file_id, f.project
            ORDER BY f.modified_at DESC
        """, (search_term, search_term, search_term)).fetchall()

    _print_file_results(rows)


def cmd_lookup(file_id: str = None, name: str = None, project: str = None):
    """Lookup by exact ID or partial name."""
    db = get_db()

    if file_id:
        if project:
            rows = db.execute("""
                SELECT f.*, GROUP_CONCAT(t.tag) as tags
                FROM files f
                LEFT JOIN file_tags t ON f.file_id = t.file_id AND f.project = t.project
                WHERE f.file_id = ? AND f.project = ?
                GROUP BY f.file_id, f.project
            """, (file_id, project)).fetchall()
        else:
            rows = db.execute("""
                SELECT f.*, GROUP_CONCAT(t.tag) as tags
                FROM files f
                LEFT JOIN file_tags t ON f.file_id = t.file_id AND f.project = t.project
                WHERE f.file_id = ?
                GROUP BY f.file_id, f.project
            """, (file_id,)).fetchall()
    elif name:
        search_term = f"%{name}%"
        if project:
            rows = db.execute("""
                SELECT f.*, GROUP_CONCAT(t.tag) as tags
                FROM files f
                LEFT JOIN file_tags t ON f.file_id = t.file_id AND f.project = t.project
                WHERE f.project = ? AND f.name LIKE ?
                GROUP BY f.file_id, f.project
                ORDER BY f.modified_at DESC
            """, (project, search_term)).fetchall()
        else:
            rows = db.execute("""
                SELECT f.*, GROUP_CONCAT(t.tag) as tags
                FROM files f
                LEFT JOIN file_tags t ON f.file_id = t.file_id AND f.project = t.project
                WHERE f.name LIKE ?
                GROUP BY f.file_id, f.project
                ORDER BY f.modified_at DESC
            """, (search_term,)).fetchall()
    else:
        print(json.dumps({"error": "lookup requires --id or --name"}), file=sys.stderr)
        sys.exit(1)

    _print_file_results(rows)


def cmd_list(project: str = None, mime: str = None, tag: str = None, folder: str = None,
             limit: int = 50):
    """Filtered list of files."""
    db = get_db()
    conditions = []
    params = []

    if project:
        conditions.append("f.project = ?")
        params.append(project)

    if mime:
        resolved_mime = MIME_SHORTCUTS.get(mime, mime)
        conditions.append("f.mime_type = ?")
        params.append(resolved_mime)

    if tag:
        conditions.append("""f.file_id IN (
            SELECT file_id FROM file_tags WHERE tag = ?
        )""")
        params.append(tag)

    if folder:
        folder_term = f"%{folder}%"
        conditions.append("(f.parent_folder_name LIKE ? OR f.path LIKE ?)")
        params.extend([folder_term, folder_term])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)

    rows = db.execute(f"""
        SELECT f.*, GROUP_CONCAT(t.tag) as tags
        FROM files f
        LEFT JOIN file_tags t ON f.file_id = t.file_id AND f.project = t.project
        {where}
        GROUP BY f.file_id, f.project
        ORDER BY f.modified_at DESC
        LIMIT ?
    """, params).fetchall()

    _print_file_results(rows)


def cmd_refs(file_id: str = None, name: str = None):
    """Show which local files reference a given Google Doc."""
    db = get_db()

    if name and not file_id:
        # Resolve name to file_id first
        row = db.execute(
            "SELECT file_id FROM files WHERE name LIKE ? LIMIT 1",
            (f"%{name}%",)
        ).fetchone()
        if row:
            file_id = row["file_id"]
        else:
            print(json.dumps({"error": f"No file found matching '{name}'"}), file=sys.stderr)
            sys.exit(1)

    if not file_id:
        print(json.dumps({"error": "refs requires --id or --name"}), file=sys.stderr)
        sys.exit(1)

    rows = db.execute("""
        SELECT lr.*, f.name as file_name, f.project as file_project
        FROM local_references lr
        LEFT JOIN files f ON lr.file_id = f.file_id AND lr.project = f.project
        WHERE lr.file_id = ?
        ORDER BY lr.project, lr.local_file_path
    """, (file_id,)).fetchall()

    results = []
    for r in rows:
        results.append({
            "file_id": r["file_id"],
            "file_name": r["file_name"],
            "project": r["project"],
            "local_file_path": r["local_file_path"],
            "context": r["context"],
            "discovered_at": r["discovered_at"],
        })

    print(json.dumps({"file_id": file_id, "references": results, "count": len(results)}, indent=2))


def cmd_stats():
    """Summary statistics."""
    db = get_db()

    # File counts by project
    project_counts = db.execute("""
        SELECT project, COUNT(*) as count, COUNT(DISTINCT mime_type) as mime_types
        FROM files GROUP BY project
    """).fetchall()

    # File counts by MIME type
    mime_counts = db.execute("""
        SELECT mime_type, project, COUNT(*) as count
        FROM files GROUP BY mime_type, project ORDER BY count DESC
    """).fetchall()

    # Folder counts
    folder_counts = db.execute("""
        SELECT project, COUNT(*) as count FROM folders GROUP BY project
    """).fetchall()

    # Local reference counts
    ref_counts = db.execute("""
        SELECT project, COUNT(*) as count FROM local_references GROUP BY project
    """).fetchall()

    # Tag counts
    tag_counts = db.execute("""
        SELECT tag, COUNT(*) as count FROM file_tags GROUP BY tag ORDER BY count DESC
    """).fetchall()

    # Last crawl per project
    last_crawls = db.execute("""
        SELECT project, operation, completed_at, files_discovered, files_updated, status
        FROM crawl_log
        WHERE id IN (SELECT MAX(id) FROM crawl_log GROUP BY project, operation)
        ORDER BY project, operation
    """).fetchall()

    output = {
        "files_by_project": {r["project"]: r["count"] for r in project_counts},
        "files_by_type": [
            {"mime_type": r["mime_type"], "project": r["project"], "count": r["count"]}
            for r in mime_counts
        ],
        "folders_by_project": {r["project"]: r["count"] for r in folder_counts},
        "local_references_by_project": {r["project"]: r["count"] for r in ref_counts},
        "tags": {r["tag"]: r["count"] for r in tag_counts},
        "last_operations": [
            {
                "project": r["project"], "operation": r["operation"],
                "completed_at": r["completed_at"], "status": r["status"],
                "files_discovered": r["files_discovered"],
                "files_updated": r["files_updated"],
            }
            for r in last_crawls
        ],
        "total_files": sum(r["count"] for r in project_counts) if project_counts else 0,
        "total_folders": sum(r["count"] for r in folder_counts) if folder_counts else 0,
        "total_references": sum(r["count"] for r in ref_counts) if ref_counts else 0,
    }

    print(json.dumps(output, indent=2))


# =============================================================================
# Tag commands
# =============================================================================


def cmd_tag(file_id: str, project: str, tags: list[str]):
    """Add tags to a file."""
    db = get_db()
    added = 0
    for tag in tags:
        tag = tag.strip().lower()
        if not tag:
            continue
        db.execute("""
            INSERT OR IGNORE INTO file_tags (file_id, project, tag) VALUES (?, ?, ?)
        """, (file_id, project, tag))
        added += 1
    db.commit()
    print(json.dumps({"file_id": file_id, "project": project, "tags_added": added}, indent=2))


def cmd_untag(file_id: str, project: str, tags: list[str]):
    """Remove tags from a file."""
    db = get_db()
    removed = 0
    for tag in tags:
        tag = tag.strip().lower()
        if not tag:
            continue
        cursor = db.execute("""
            DELETE FROM file_tags WHERE file_id = ? AND project = ? AND tag = ?
        """, (file_id, project, tag))
        removed += cursor.rowcount
    db.commit()
    print(json.dumps({"file_id": file_id, "project": project, "tags_removed": removed}, indent=2))


# =============================================================================
# Helpers
# =============================================================================


def _print_file_results(rows):
    """Format and print file query results as JSON."""
    results = []
    for r in rows:
        entry = {
            "file_id": r["file_id"],
            "project": r["project"],
            "name": r["name"],
            "mime_type": r["mime_type"],
            "url": r["url"],
            "path": r["path"],
            "modified_at": r["modified_at"],
            "owner_email": r["owner_email"],
        }
        if r["tags"]:
            entry["tags"] = r["tags"].split(",")
        results.append(entry)

    print(json.dumps({"results": results, "count": len(results)}, indent=2))


def _start_crawl_log(db: sqlite3.Connection, project: str, operation: str) -> int:
    """Start a crawl log entry, return its ID."""
    cursor = db.execute("""
        INSERT INTO crawl_log (project, operation, started_at) VALUES (?, ?, ?)
    """, (project, operation, _now()))
    db.commit()
    return cursor.lastrowid


def _finish_crawl_log(db: sqlite3.Connection, log_id: int,
                      files_discovered: int, files_updated: int,
                      error: str = None):
    """Complete a crawl log entry."""
    status = "error" if error else "completed"
    db.execute("""
        UPDATE crawl_log SET
            files_discovered = ?,
            files_updated = ?,
            completed_at = ?,
            status = ?,
            error = ?
        WHERE id = ?
    """, (files_discovered, files_updated, _now(), status, error, log_id))
    db.commit()


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Google Drive SQLite index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # seed
    p_seed = subparsers.add_parser("seed", help="Parse markdown files for Google URLs")
    p_seed.add_argument("--project", required=True, help="anaro-labs or estate-mate")

    # crawl
    p_crawl = subparsers.add_parser("crawl", help="Recursive BFS crawl of Drive")
    p_crawl.add_argument("--project", required=True, help="anaro-labs or estate-mate")
    p_crawl.add_argument("--folder", help="Start folder ID (default: root)")
    p_crawl.add_argument("--max-depth", type=int, default=10, help="Max traversal depth")

    # refresh
    p_refresh = subparsers.add_parser("refresh", help="Incremental update")
    p_refresh.add_argument("--project", required=True, help="anaro-labs or estate-mate")
    p_refresh.add_argument("--id", dest="file_id", help="Single file ID to refresh")

    # search
    p_search = subparsers.add_parser("search", help="Search by name/keyword")
    p_search.add_argument("query", help="Search term")
    p_search.add_argument("--project", help="Filter by project")

    # lookup
    p_lookup = subparsers.add_parser("lookup", help="Lookup by ID or name")
    p_lookup.add_argument("--id", dest="file_id", help="Exact file ID")
    p_lookup.add_argument("--name", help="Partial name match")
    p_lookup.add_argument("--project", help="Filter by project")

    # list
    p_list = subparsers.add_parser("list", help="Filtered list")
    p_list.add_argument("--project", help="Filter by project")
    p_list.add_argument("--mime", help="MIME type or shortcut (doc, sheet, slides, pdf)")
    p_list.add_argument("--tag", help="Filter by tag")
    p_list.add_argument("--folder", help="Filter by folder name")
    p_list.add_argument("--limit", type=int, default=50, help="Max results")

    # refs
    p_refs = subparsers.add_parser("refs", help="Show local file references")
    p_refs.add_argument("--id", dest="file_id", help="File ID")
    p_refs.add_argument("--name", help="Partial name to resolve")

    # stats
    subparsers.add_parser("stats", help="Summary statistics")

    # tag
    p_tag = subparsers.add_parser("tag", help="Add tags to a file")
    p_tag.add_argument("--id", dest="file_id", required=True, help="File ID")
    p_tag.add_argument("--project", required=True, help="Project")
    p_tag.add_argument("--tags", required=True, help="Comma-separated tags")

    # untag
    p_untag = subparsers.add_parser("untag", help="Remove tags")
    p_untag.add_argument("--id", dest="file_id", required=True, help="File ID")
    p_untag.add_argument("--project", required=True, help="Project")
    p_untag.add_argument("--tags", required=True, help="Comma-separated tags")

    args = parser.parse_args()

    if args.command == "seed":
        cmd_seed(_resolve_project(args.project))
    elif args.command == "crawl":
        cmd_crawl(_resolve_project(args.project), folder_id=args.folder, max_depth=args.max_depth)
    elif args.command == "refresh":
        cmd_refresh(_resolve_project(args.project), file_id=args.file_id)
    elif args.command == "search":
        cmd_search(args.query, project=_resolve_project(args.project) if args.project else None)
    elif args.command == "lookup":
        cmd_lookup(
            file_id=args.file_id, name=args.name,
            project=_resolve_project(args.project) if args.project else None,
        )
    elif args.command == "list":
        cmd_list(
            project=_resolve_project(args.project) if args.project else None,
            mime=args.mime, tag=args.tag, folder=args.folder, limit=args.limit,
        )
    elif args.command == "refs":
        cmd_refs(file_id=args.file_id, name=args.name)
    elif args.command == "stats":
        cmd_stats()
    elif args.command == "tag":
        cmd_tag(args.file_id, _resolve_project(args.project), args.tags.split(","))
    elif args.command == "untag":
        cmd_untag(args.file_id, _resolve_project(args.project), args.tags.split(","))


if __name__ == "__main__":
    main()
