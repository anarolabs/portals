# DocSync - Implementation plan

**Project**: Multi-platform document synchronization tool
**Created**: 2025-11-11
**Status**: Planning phase
**Owner**: Roman Siepelmeyer

---

## Executive summary

Build a tool to keep local markdown files in sync with Notion, Google Docs, and Obsidian, with three distinct operating modes:

1. **Mirror mode** (Notion): Entire `~/Documents/Claude Code/` directory ↔ Notion workspace
2. **Pair mode** (Google Docs): Selective individual document pairing
3. **Import mode** (Obsidian): One-way import for inspiration

**Primary focus**: Notion mirror mode (80% of value)

---

## Use cases and workflows

### Use case 1: Notion as working knowledge base (PRIMARY)

**Goal**: Keep all Claude Code projects synced with Notion bidirectionally

**Workflow**:
```
~/Documents/Claude Code/
├── project-a/
│   ├── 01-context/
│   │   └── architecture.md       ↔  Notion Page
│   ├── 02-research/
│   │   └── findings.md           ↔  Notion Page
│   └── AGENT_CONTEXT.md          ↔  Notion Page
├── project-b/
│   └── notes.md                  ↔  Notion Page
└── templates/
    └── project-template.md       ↔  Notion Page
```

**Characteristics**:
- **Automatic**: All markdown files automatically paired
- **Hierarchical**: Directory structure = Notion page hierarchy
- **Bidirectional**: Edit locally or in Notion, syncs both ways
- **Always on**: Watch mode keeps everything synced
- **Semi-automatic**: Prompts before sync, manual conflict resolution

**Commands**:
```bash
cd ~/Documents/Claude\ Code/
docsync init notion-mirror --database=<notion-database-id>
docsync watch  # Runs continuously
```

### Use case 2: Selective Google Docs collaboration (OCCASIONAL)

**Goal**: Sync specific documents with collaborators via Google Docs

**Workflow**:
```
~/Documents/Claude Code/collaboration/
├── q4-report.md              ↔  Google Doc ABC123
└── presentation-draft.md     ↔  Google Doc XYZ789
```

**Characteristics**:
- **Manual pairing**: Explicitly pair each document
- **Selective**: Only paired files sync
- **Bidirectional**: Edit locally or in Google Docs
- **On-demand**: Sync when you want

**Commands**:
```bash
docsync pair q4-report.md gdoc://abc123
docsync sync q4-report.md
```

### Use case 3: Obsidian inspiration (READ-ONLY)

**Goal**: Import useful content from Obsidian vault to Notion/local

**Workflow**:
```
Obsidian Vault
    ↓ (one-way import)
Notion or Local Markdown
```

**Characteristics**:
- **One-way**: Import only, no tracking
- **On-demand**: Run when you need it
- **Format conversion**: Wiki-links → Markdown links

**Commands**:
```bash
# Import to local file
docsync import obsidian://vault/research.md --output=local/inspiration.md

# Import directly to Notion
docsync import obsidian://vault/research.md --to=notion://page-id
```

---

## Notion/Obsidian database strategy

**Recommendation**: Use Notion as primary, Obsidian as read-only archive

### Rationale:

**Notion as active workspace** because:
- Better API for automation (official SDK)
- Better collaboration features (share, comment, permissions)
- Web-based (access anywhere)
- Works seamlessly with Claude Code
- Database features (properties, filters, views)
- Better for structured content

**Obsidian as historical archive** because:
- Already contains existing notes
- Local-first is good for long-term preservation
- Great for personal knowledge base
- No need to migrate everything at once
- Can pull from it when needed

### Migration path:

1. **Start fresh with Notion**: All new Claude Code work goes to Notion (via local mirror)
2. **Reference Obsidian**: Keep it as read-only vault
3. **Import as needed**: When you find something useful in Obsidian, import to Notion
4. **Gradual consolidation**: Over time, valuable Obsidian content moves to Notion
5. **Final state**: Notion is primary, Obsidian is archived

**No bidirectional sync needed** between Notion and Obsidian. They serve different purposes.

---

## System architecture

### High-level architecture

```
┌─────────────────────────────────────────────┐
│  CLI Layer (Click)                          │
│  - init (notion-mirror, pair-mode)          │
│  - pair / unpair                            │
│  - sync / push / pull                       │
│  - watch                                    │
│  - import (one-way)                         │
│  - status                                   │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Mode Controllers                           │
│  - MirrorModeController                     │
│  - PairModeController                       │
│  - ImportModeController                     │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Core Services                              │
│  - SyncService (3-way merge)                │
│  - DirectoryScanner (recursive scan)        │
│  - HierarchyMapper (folders → pages)        │
│  - ConflictResolver                         │
│  - WatchService                             │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Adapters                                   │
│  - LocalFileAdapter                         │
│  - NotionAdapter (pages, hierarchy)         │
│  - GoogleDocsAdapter (via MCP)              │
│  - ObsidianAdapter (read-only)              │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Storage                                    │
│  - MetadataStore (.portals/metadata.json)   │
│  - ConfigStore (.portals/config.json)       │
└─────────────────────────────────────────────┘
```

### Operating modes

**Mode stored in `.portals/config.json`:**

```json
{
  "mode": "notion-mirror",  // or "pair-mode"
  "notion": {
    "database_id": "abc123...",
    "root_page_id": "def456..."
  },
  "sync": {
    "auto_sync": false,
    "watch_enabled": true,
    "conflict_resolution": "manual"
  }
}
```

### Notion hierarchy mapping

**Local directory structure** maps to **Notion parent-child pages**:

```
Local:                          Notion:
─────────────────────          ─────────────────────
project-a/                 →   📄 Project A (parent page)
├── 01-context/           →     📄 01-context (child)
│   └── arch.md           →       📄 Architecture
├── 02-research/          →     📄 02-research (child)
│   └── findings.md       →       📄 Findings
└── AGENT_CONTEXT.md      →     📄 Agent Context
```

**Folder-to-page convention**:
- Folder becomes a Notion page with title = folder name
- Markdown files become child pages of their parent folder
- Maintains hierarchical structure in Notion

### Metadata storage

**.portals/metadata.json** (for mirror mode):

```json
{
  "version": "1.0.0",
  "mode": "notion-mirror",
  "root": "/Users/user/Documents/Claude Code",
  "notion": {
    "database_id": "abc123",
    "root_page_id": "root-page-id"
  },
  "files": [
    {
      "local_path": "project-a/01-context/architecture.md",
      "notion_page_id": "page-abc123",
      "last_sync": "2025-11-11T15:30:00Z",
      "local_hash": "sha256:...",
      "notion_hash": "sha256:...",
      "has_conflict": false
    }
  ],
  "hierarchy": {
    "project-a": {
      "notion_page_id": "parent-page-id",
      "children": {
        "01-context": {
          "notion_page_id": "context-page-id",
          "children": {}
        }
      }
    }
  }
}
```

**.portals/metadata.json** (for pair mode):

```json
{
  "version": "1.0.0",
  "mode": "pair-mode",
  "pairs": [
    {
      "local_path": "report.md",
      "remote_uri": "gdoc://abc123",
      "platform": "google-docs",
      "last_sync": "2025-11-11T10:00:00Z",
      "local_hash": "sha256:...",
      "remote_hash": "sha256:...",
      "has_conflict": false
    }
  ]
}
```

---

## Implementation phases

### Phase 0: Foundation (3-5 days)

**Goal**: Set up project infrastructure and base classes

**Tasks**:
- [ ] Initialize project with `uv` (pyproject.toml)
- [ ] Create directory structure
- [ ] Set up dependencies (click, notion-client, watchdog, rich, structlog)
- [ ] Configure pytest with fixtures
- [ ] Set up pre-commit hooks (ruff, mypy)
- [ ] Create base abstract classes:
  - `DocumentAdapter` (abstract)
  - `Document` (data model)
  - `SyncResult` (result types)
- [ ] Set up logging framework
- [ ] Create CLI skeleton with Click

**Deliverables**:
- Working Python project
- `uv run docsync --help` shows CLI
- Tests pass (even if empty)
- Type checking passes

**Files created**:
```
docsync/
├── pyproject.toml
├── docsync/
│   ├── __init__.py
│   ├── __main__.py
│   ├── core/
│   │   └── models.py          # Document, SyncResult
│   ├── adapters/
│   │   └── base.py            # DocumentAdapter abstract
│   ├── cli/
│   │   └── main.py            # CLI entry point
│   └── utils/
│       └── logging.py
└── tests/
    └── conftest.py
```

---

### Phase 1: Local file operations (3-5 days)

**Goal**: Read/write local markdown files and manage metadata

**Components**:

1. **LocalFileAdapter**
   - Read/write markdown files
   - Parse YAML front matter
   - Calculate SHA-256 content hash
   - List files in directory recursively
   - Handle file system errors

2. **MetadataStore**
   - Initialize `.portals/` directory
   - Read/write metadata.json
   - Atomic writes (temp file + rename)
   - Schema validation

3. **DirectoryScanner**
   - Recursively scan directory for markdown files
   - Build file tree
   - Filter files (ignore .portals/, .git/, etc.)

**Key files**:
```
docsync/
├── adapters/
│   └── local.py               # LocalFileAdapter
├── core/
│   ├── metadata_store.py      # MetadataStore
│   ├── directory_scanner.py   # DirectoryScanner
│   └── hashing.py             # SHA-256 utilities
└── utils/
    └── markdown.py            # Markdown parsing
```

**Tests**:
```python
test_local_adapter_read()
test_local_adapter_write()
test_local_adapter_hash_content()
test_local_adapter_list_files()
test_metadata_store_init()
test_metadata_store_save_load()
test_metadata_store_atomic_write()
test_directory_scanner_recursive()
test_directory_scanner_filters()
```

**Acceptance criteria**:
- Can read/write markdown files
- Content hashing is consistent
- Metadata persists correctly
- Directory scanning works recursively
- 90%+ test coverage

---

### Phase 2: Notion adapter (5-7 days)

**Goal**: Read/write Notion pages and maintain hierarchy

**Components**:

1. **NotionAdapter**
   - Initialize Notion client (API key)
   - Read page content → markdown
   - Write markdown → Notion blocks
   - Get page metadata (title, last_edited_time)
   - Create pages with parent relationships
   - List child pages

2. **NotionBlockConverter**
   - Markdown → Notion blocks
   - Notion blocks → Markdown
   - Support block types:
     - Paragraph, headings (h1, h2, h3)
     - Bulleted list, numbered list
     - Code blocks
     - Quotes
     - Images (upload)

3. **NotionHierarchyManager**
   - Create parent-child page relationships
   - Map folder structure to page hierarchy
   - Query page children
   - Maintain hierarchy metadata

**Key files**:
```
docsync/
├── adapters/
│   └── notion/
│       ├── adapter.py         # NotionAdapter
│       ├── converter.py       # Block conversion
│       ├── hierarchy.py       # Hierarchy management
│       └── client.py          # Notion API wrapper
└── config/
    └── notion_config.py       # API key management
```

**Configuration**:
```bash
# .env or config
NOTION_API_KEY=secret_abc...
NOTION_DATABASE_ID=database-id  # For mirror mode
```

**Example usage**:
```python
# Read page
adapter = NotionAdapter(api_key=os.getenv("NOTION_API_KEY"))
doc = await adapter.read("page-id")
print(doc.content)  # Markdown

# Create page with parent
await adapter.create_page(
    title="Architecture",
    content="# Architecture\n\nDetails...",
    parent_page_id="parent-id"
)

# List children
children = await adapter.list_children("parent-id")
```

**Tests**:
```python
test_notion_adapter_read_page()
test_notion_adapter_create_page()
test_notion_adapter_update_page()
test_notion_block_converter_md_to_blocks()
test_notion_block_converter_blocks_to_md()
test_notion_hierarchy_create_parent_child()
test_notion_hierarchy_list_children()
```

**Acceptance criteria**:
- Read Notion pages correctly
- Write markdown to Notion pages
- Create parent-child relationships
- Block conversion works for common types
- API errors handled gracefully
- 85%+ test coverage

---

### Phase 3: Mirror mode initialization (3-4 days)

**Goal**: Set up mirror mode and initial sync

**Components**:

1. **MirrorModeController**
   - Initialize mirror mode
   - Scan local directory
   - Create Notion hierarchy
   - Perform initial sync (local → Notion)
   - Save metadata

2. **HierarchyMapper**
   - Map directory structure to Notion pages
   - Create parent pages for folders
   - Maintain hierarchy metadata
   - Handle nested directories

**Key files**:
```
docsync/
├── controllers/
│   └── mirror_mode.py         # MirrorModeController
├── core/
│   └── hierarchy_mapper.py    # HierarchyMapper
└── cli/
    └── init.py                # Init command
```

**Init workflow**:

```bash
$ cd ~/Documents/Claude\ Code/
$ docsync init notion-mirror --database=<database-id>

🔍 Scanning directory...
   Found 15 markdown files in 5 folders

📄 Creating Notion hierarchy...
   ✓ Created parent page: project-a
   ✓ Created child page: 01-context
   ✓ Created child page: 02-research
   ...

⬆️  Performing initial sync (local → Notion)...
   ✓ Synced project-a/01-context/architecture.md
   ✓ Synced project-a/02-research/findings.md
   ...

✅ Mirror mode initialized!
   15 files synced to Notion

   Run 'docsync watch' to keep in sync
```

**Initialization logic**:

1. Scan local directory recursively
2. Build hierarchy tree (folders + files)
3. Create Notion pages for folders (parent pages)
4. Create Notion pages for markdown files (child pages)
5. Upload markdown content
6. Save metadata mapping (local path → Notion page ID)
7. Calculate initial hashes

**Tests**:
```python
test_mirror_mode_init()
test_mirror_mode_scan_directory()
test_mirror_mode_create_hierarchy()
test_mirror_mode_initial_sync()
test_hierarchy_mapper_build_tree()
test_hierarchy_mapper_create_notion_structure()
```

**Acceptance criteria**:
- Successfully scans directory
- Creates correct Notion hierarchy
- Initial sync uploads all files
- Metadata saved correctly
- Handles errors gracefully

---

### Phase 4: Bidirectional sync (5-7 days)

**Goal**: Implement push/pull with conflict detection

**Components**:

1. **SyncEngine**
   - 3-way merge algorithm (local, remote, base)
   - Push: local → Notion
   - Pull: Notion → local
   - Detect conflicts
   - Update metadata after sync

2. **ConflictDetector**
   - Compare hashes (local, remote, base)
   - Determine sync direction
   - Identify conflict scenarios:
     - Both modified
     - One deleted, one modified
     - Both deleted

3. **SyncService**
   - High-level sync orchestration
   - Sync single file
   - Sync all files
   - Handle errors and rollback

**Key files**:
```
docsync/
├── core/
│   ├── sync_engine.py         # Core sync logic
│   ├── conflict_detector.py   # Conflict detection
│   └── change_detector.py     # Change detection
└── services/
    └── sync_service.py        # High-level service
```

**3-way merge algorithm**:

```python
def sync(local: Document, remote: Document, base: Document) -> SyncResult:
    """3-way merge"""

    local_hash = hash(local.content)
    remote_hash = hash(remote.content)
    base_hash = hash(base.content)

    if local_hash == base_hash and remote_hash == base_hash:
        return SyncResult.NO_CHANGES

    elif local_hash != base_hash and remote_hash == base_hash:
        # Local changed, remote unchanged → PUSH
        return SyncResult.PUSH

    elif local_hash == base_hash and remote_hash != base_hash:
        # Remote changed, local unchanged → PULL
        return SyncResult.PULL

    elif local_hash == remote_hash:
        # Both changed identically → UPDATE BASE
        return SyncResult.IDENTICAL_CHANGES

    else:
        # Both changed differently → CONFLICT
        return SyncResult.CONFLICT
```

**CLI commands**:
```bash
# Sync single file
docsync sync project-a/notes.md

# Sync all files
docsync sync --all

# Force push (ignore conflicts)
docsync push project-a/notes.md --force

# Force pull (ignore conflicts)
docsync pull project-a/notes.md --force

# Check status
docsync status
```

**Status output**:
```bash
$ docsync status

DocSync Status (Mirror Mode)
─────────────────────────────────────────

✓ 12 files in sync
⚠️ 3 conflicts
↑ 2 local changes (ready to push)
↓ 1 remote change (ready to pull)

Files needing attention:
─────────────────────────────────────────

⚠️ project-a/notes.md
   Status: CONFLICT (both modified)
   Last sync: 2 hours ago
   → Run: docsync resolve project-a/notes.md

↑ project-b/research.md
   Status: Local modified
   Last sync: 1 hour ago
   → Run: docsync sync project-b/research.md

↓ templates/template.md
   Status: Remote modified
   Last sync: 30 minutes ago
   → Run: docsync sync templates/template.md
```

**Tests**:
```python
test_sync_engine_no_changes()
test_sync_engine_push()
test_sync_engine_pull()
test_sync_engine_identical_changes()
test_sync_engine_conflict()
test_conflict_detector_scenarios()
test_sync_service_sync_file()
test_sync_service_sync_all()
```

**Acceptance criteria**:
- Push/pull work correctly
- Conflicts detected accurately
- Metadata updated after sync
- Error handling and rollback
- 90%+ test coverage

---

### Phase 5: Conflict resolution (4-5 days)

**Goal**: Manual conflict resolution with diff viewer

**Components**:

1. **ConflictResolver**
   - Show diff between local and remote
   - Prompt user for resolution
   - Apply resolution choice
   - Update metadata

2. **DiffGenerator**
   - Generate unified diff
   - Highlight changes
   - Side-by-side comparison

**Key files**:
```
docsync/
├── core/
│   ├── conflict_resolver.py   # Resolution strategies
│   └── diff_generator.py      # Diff utilities
└── cli/
    └── resolve.py             # Resolve command
```

**Conflict resolution UI**:

```bash
$ docsync resolve project-a/notes.md

⚠️  Conflict detected: project-a/notes.md

Both local and Notion versions have changed since last sync.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📄 LOCAL (modified 2 hours ago):
───────────────────────────────────────
1  # Meeting Notes
2
3- - Discussed Q1 roadmap
4+ - Action items: 5
5
6  Next steps...

📄 NOTION (modified 1 hour ago):
───────────────────────────────────────
1  # Meeting Notes
2
3+ - Reviewed Q1 roadmap
4+ - Action items: 3
5
6  Next steps...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

How would you like to resolve?

[L] Use Local version
[R] Use Remote (Notion) version
[M] Merge manually (open editor)
[D] Show detailed diff
[C] Cancel

Choice: _
```

**Resolution strategies**:

1. **Use local**: Overwrite Notion with local version
2. **Use remote**: Overwrite local with Notion version
3. **Merge manually**: Open editor with conflict markers
4. **Show diff**: Display detailed diff

**Conflict markers** (for manual merge):
```markdown
# Meeting Notes

<<<<<<< LOCAL
- Discussed Q1 roadmap
- Action items: 5
=======
- Reviewed Q1 roadmap
- Action items: 3
>>>>>>> NOTION

Next steps...
```

**Tests**:
```python
test_conflict_resolver_use_local()
test_conflict_resolver_use_remote()
test_conflict_resolver_manual_merge()
test_diff_generator_unified_diff()
test_diff_generator_side_by_side()
```

**Acceptance criteria**:
- Conflicts displayed clearly
- All resolution strategies work
- Manual merge opens editor
- Metadata updated after resolution
- Good UX with rich output

---

### Phase 6: Watch mode (3-5 days)

**Goal**: Continuously watch for changes and prompt to sync

**Components**:

1. **FileWatcher**
   - Watch directory with watchdog
   - Debounce changes (2 second delay)
   - Filter relevant files (only .md, ignore .portals/)
   - Queue changes for processing

2. **WatchService**
   - Start/stop watching
   - Process file events
   - Prompt user before syncing
   - Handle multiple files

3. **NotionPoller**
   - Poll Notion for remote changes
   - Check page last_edited_time
   - Queue remote changes

**Key files**:
```
docsync/
├── watcher/
│   ├── file_watcher.py        # Local file watching
│   ├── notion_poller.py       # Poll Notion for changes
│   └── watch_service.py       # Orchestration
└── cli/
    └── watch.py               # Watch command
```

**Watch command**:
```bash
# Start watching
docsync watch

# Auto-sync mode (no prompts)
docsync watch --auto

# Watch specific path
docsync watch project-a/
```

**Watch mode UI**:

```bash
$ docsync watch

🔍 Watching ~/Documents/Claude Code/
   Press Ctrl+C to stop

[15:30:45] Change detected: project-a/notes.md
           Local file modified

           Sync to Notion?
           [Y] Yes  [N] No  [A] Always  [Q] Quit

           Choice: y

[15:30:46] ✓ Synced project-a/notes.md → Notion

[15:32:10] Change detected: templates/template.md
           Remote (Notion) modified

           Pull from Notion?
           [Y] Yes  [N] No  [A] Always  [Q] Quit

           Choice: y

[15:32:11] ✓ Synced Notion → templates/template.md
```

**Watch workflow**:

1. Monitor local file system (watchdog)
2. Poll Notion every 30 seconds for remote changes
3. When change detected:
   - Debounce (wait 2 seconds)
   - Determine sync direction
   - Prompt user
   - Perform sync
   - Update metadata
4. Repeat

**Tests**:
```python
test_file_watcher_detects_local_change()
test_file_watcher_debounces()
test_notion_poller_detects_remote_change()
test_watch_service_prompts_user()
test_watch_service_auto_mode()
```

**Acceptance criteria**:
- Detects local file changes
- Detects remote Notion changes
- Debouncing works correctly
- Prompts user appropriately
- Auto mode works without prompts
- Graceful shutdown

---

### Phase 7: Google Docs pairing (3-5 days)

**Goal**: Add selective Google Docs pairing

**Components**:

1. **GoogleDocsAdapter**
   - Use Google Workspace MCP
   - Read Google Doc content
   - Write to Google Docs
   - Format conversion

2. **PairModeController**
   - Initialize pair mode
   - Create manual pairs
   - Sync paired documents

**Key files**:
```
docsync/
├── adapters/
│   └── gdocs/
│       ├── adapter.py         # GoogleDocsAdapter
│       ├── mcp_client.py      # MCP integration
│       └── converter.py       # Format conversion
└── controllers/
    └── pair_mode.py           # PairModeController
```

**Pair commands**:
```bash
# Initialize pair mode
docsync init pair-mode

# Create pair
docsync pair report.md gdoc://abc123

# List pairs
docsync list

# Sync pair
docsync sync report.md

# Remove pair
docsync unpair report.md
```

**Tests**:
```python
test_gdocs_adapter_read()
test_gdocs_adapter_write()
test_pair_mode_create_pair()
test_pair_mode_sync_pair()
```

**Acceptance criteria**:
- Can read Google Docs via MCP
- Can write to Google Docs via MCP
- Pairing works correctly
- Sync logic same as mirror mode
- 85%+ test coverage

---

### Phase 8: Obsidian import (2-3 days)

**Goal**: One-way import from Obsidian

**Components**:

1. **ObsidianAdapter**
   - Read Obsidian notes (read-only)
   - Parse wiki-links
   - Convert to standard markdown

2. **ImportModeController**
   - Import single file
   - Convert wiki-links
   - Write to local or Notion

**Key files**:
```
docsync/
├── adapters/
│   └── obsidian/
│       ├── adapter.py         # ObsidianAdapter (read-only)
│       └── wikilink_parser.py # Wiki-link conversion
└── controllers/
    └── import_mode.py         # ImportModeController
```

**Import commands**:
```bash
# Import to local file
docsync import obsidian://vault/note.md --output=local/imported.md

# Import to Notion
docsync import obsidian://vault/note.md --to=notion://page-id
```

**Wiki-link conversion**:
```markdown
# Before (Obsidian)
See [[Other Note]] for details.
Action: [[John Smith]] to review.

# After (Markdown)
See [Other Note](other-note.md) for details.
Action: [John Smith](john-smith.md) to review.
```

**Tests**:
```python
test_obsidian_adapter_read()
test_wikilink_parser_convert()
test_import_to_local()
test_import_to_notion()
```

**Acceptance criteria**:
- Reads Obsidian notes correctly
- Wiki-links converted properly
- Can import to local or Notion
- Simple, focused feature

---

## Technical specifications

### Technology stack

**Core**:
- Python 3.11+
- uv for dependency management
- Click for CLI
- asyncio for async operations

**Libraries**:
- `notion-client` - Notion API SDK
- `watchdog` - File system monitoring
- `python-frontmatter` - YAML front matter
- `rich` - Beautiful terminal UI
- `structlog` - Structured logging
- `pydantic` - Data validation
- `pytest` + `pytest-asyncio` - Testing

**Development**:
- `ruff` - Linting and formatting
- `mypy` - Type checking
- `pre-commit` - Git hooks

### Project structure

```
docsync/
├── docsync/                      # Main package
│   ├── __init__.py
│   ├── __main__.py
│   │
│   ├── cli/                      # CLI commands
│   │   ├── main.py               # Entry point
│   │   ├── init.py               # Init commands
│   │   ├── sync.py               # Sync commands
│   │   ├── watch.py              # Watch command
│   │   ├── resolve.py            # Resolve command
│   │   ├── pair.py               # Pair/unpair
│   │   └── import_cmd.py         # Import command
│   │
│   ├── controllers/              # Mode controllers
│   │   ├── mirror_mode.py
│   │   ├── pair_mode.py
│   │   └── import_mode.py
│   │
│   ├── services/                 # Business logic
│   │   ├── sync_service.py
│   │   └── watch_service.py
│   │
│   ├── core/                     # Core engine
│   │   ├── models.py
│   │   ├── sync_engine.py
│   │   ├── conflict_detector.py
│   │   ├── conflict_resolver.py
│   │   ├── diff_generator.py
│   │   ├── metadata_store.py
│   │   ├── directory_scanner.py
│   │   ├── hierarchy_mapper.py
│   │   └── hashing.py
│   │
│   ├── adapters/                 # Platform adapters
│   │   ├── base.py
│   │   ├── local.py
│   │   ├── notion/
│   │   │   ├── adapter.py
│   │   │   ├── converter.py
│   │   │   ├── hierarchy.py
│   │   │   └── client.py
│   │   ├── gdocs/
│   │   │   ├── adapter.py
│   │   │   ├── mcp_client.py
│   │   │   └── converter.py
│   │   └── obsidian/
│   │       ├── adapter.py
│   │       └── wikilink_parser.py
│   │
│   ├── watcher/                  # File watching
│   │   ├── file_watcher.py
│   │   ├── notion_poller.py
│   │   └── watch_service.py
│   │
│   ├── config/                   # Configuration
│   │   └── config_manager.py
│   │
│   └── utils/                    # Utilities
│       ├── markdown.py
│       ├── logging.py
│       └── validation.py
│
├── tests/                        # Tests
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── docs/                         # Documentation
│   ├── getting-started.md
│   ├── mirror-mode.md
│   ├── pair-mode.md
│   └── conflict-resolution.md
│
├── examples/                     # Examples
│   └── workflows.md
│
├── pyproject.toml
├── README.md
└── .env.example
```

### Error handling

**Exception hierarchy**:
```python
class DocSyncError(Exception):
    """Base exception"""
    pass

class ConfigError(DocSyncError):
    """Configuration errors"""
    pass

class SyncError(DocSyncError):
    """Sync errors"""
    pass

class ConflictError(SyncError):
    """Conflict detected"""
    def __init__(self, local_hash, remote_hash):
        self.local_hash = local_hash
        self.remote_hash = remote_hash

class AdapterError(DocSyncError):
    """Platform adapter errors"""
    pass

class NotionError(AdapterError):
    """Notion-specific errors"""
    pass
```

**Retry logic**:
```python
@retry(
    retry=retry_if_exception_type(NetworkError),
    wait=wait_exponential(min=1, max=10),
    stop=stop_after_attempt(3)
)
async def fetch_remote():
    ...
```

### Logging

Use `structlog` for structured logging:

```python
logger = structlog.get_logger()

logger.info("sync_started", file="notes.md", direction="push")
logger.error("sync_failed", file="notes.md", error="ConflictError")
```

### Configuration

**.portals/config.json**:
```json
{
  "mode": "notion-mirror",
  "notion": {
    "api_key": "${NOTION_API_KEY}",
    "database_id": "abc123..."
  },
  "sync": {
    "auto_sync": false,
    "conflict_resolution": "manual",
    "watch_enabled": true,
    "debounce_seconds": 2.0
  },
  "logging": {
    "level": "INFO",
    "file": ".portals/docsync.log"
  }
}
```

**Environment variables**:
```bash
NOTION_API_KEY=secret_...
GOOGLE_WORKSPACE_EMAIL=user@gmail.com
```

---

## Testing strategy

### Unit tests (85%+ coverage)

Test individual components:
- Adapters (mocked APIs)
- Sync engine
- Conflict detection
- Parsers and converters

### Integration tests

Test component interactions:
- Local ↔ Notion sync
- Hierarchy creation
- Conflict scenarios
- Watch mode

### End-to-end tests

Test complete workflows:
- Initialize mirror mode
- Sync directory
- Detect and resolve conflicts
- Watch mode

---

## Success metrics

**Functional**:
- Sync 50+ files in mirror mode successfully
- Detect and resolve conflicts correctly
- Watch mode runs for 24+ hours
- Zero data loss

**Performance**:
- Initial sync of 50 files in < 2 minutes
- Individual file sync in < 5 seconds
- Change detection in < 100ms

**Usability**:
- New user sets up in < 5 minutes
- Conflict resolution is intuitive
- Error messages are clear

**Reliability**:
- 99% success rate
- Graceful error handling
- No crashes or corruption

---

## Development timeline

**Phase 0**: Foundation (3-5 days)
**Phase 1**: Local operations (3-5 days)
**Phase 2**: Notion adapter (5-7 days)
**Phase 3**: Mirror mode init (3-4 days)
**Phase 4**: Bidirectional sync (5-7 days)
**Phase 5**: Conflict resolution (4-5 days)
**Phase 6**: Watch mode (3-5 days)
**Phase 7**: Google Docs pairing (3-5 days)
**Phase 8**: Obsidian import (2-3 days)

**Total**: 31-50 days (6-10 weeks) for full implementation

**MVP** (Phases 0-6): 21-33 days for usable Notion mirror mode

---

## Future enhancements

**Post-MVP ideas**:
1. GUI dashboard (web interface)
2. Automatic conflict resolution with AI (Claude)
3. Version history and rollback
4. Team sync (shared configuration)
5. Additional platforms (Confluence, Evernote)
6. Sync analytics and reporting
7. Git integration (auto-commit on sync)
8. Mobile app companion

---

## Questions to resolve

Before starting:

1. **Notion database vs pages**: Should we use a Notion database or just nested pages?
   - Database: Better organization, properties
   - Nested pages: Simpler hierarchy

2. **Folder representation**: How to represent folders in Notion?
   - Empty parent pages with title = folder name
   - Notion databases for each folder

3. **Attachments**: How to handle images and attachments?
   - Upload to Notion
   - Keep local references only

4. **Metadata in front matter**: Should we store Notion page ID in YAML front matter?
   ```markdown
   ---
   notion_page_id: abc123
   last_sync: 2025-11-11
   ---
   ```

5. **Git integration**: Should `.portals/` be committed to git?
   - Yes: Share sync state with team
   - No: Keep it local

---

## Next steps

Once approved:

1. ✅ Review and validate plan
2. Set up development environment
3. Begin Phase 0 implementation
4. Create initial project structure
5. Start building!

---

**This is a living document. Update as we learn and iterate.**

---

## Appendix: Command reference

### Mirror mode commands

```bash
# Initialize mirror mode
docsync init notion-mirror --database=<database-id>

# Check status
docsync status

# Sync all files
docsync sync --all

# Sync specific file
docsync sync path/to/file.md

# Watch for changes
docsync watch

# Resolve conflict
docsync resolve path/to/file.md
```

### Pair mode commands

```bash
# Initialize pair mode
docsync init pair-mode

# Create pair
docsync pair local.md gdoc://abc123

# List pairs
docsync list

# Sync pair
docsync sync local.md

# Remove pair
docsync unpair local.md
```

### Import commands

```bash
# Import from Obsidian to local
docsync import obsidian://vault/note.md --output=local.md

# Import from Obsidian to Notion
docsync import obsidian://vault/note.md --to=notion://page-id
```

### Utility commands

```bash
# Show help
docsync --help
docsync init --help

# Show version
docsync --version

# Validate configuration
docsync validate

# Show logs
docsync logs --tail=50
```
