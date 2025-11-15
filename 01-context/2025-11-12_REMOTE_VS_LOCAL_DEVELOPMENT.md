# Remote vs Local Development Assessment

**Document purpose**: Guide development workflow between Claude Code Web (remote) and Claude Code Desktop (local) for Phases 6-8

**Created**: 2025-11-12
**Status**: Planning
**Target phases**: 6 (Watch mode), 7 (Google Docs), 8 (Obsidian)

---

## Executive summary

**Key principle**: Maximize remote development in Claude Code Web, reserve Desktop only for what truly needs local file system access or real API testing.

**Rule of thumb**:
- ✅ **Claude Code Web**: Logic, algorithms, API client code, unit tests with mocks
- ⚠️ **Claude Code Desktop**: Actual file watching, real API integration, end-to-end tests

**Percentage breakdown**:
- **Phase 6** (Watch mode): ~70% Web, ~30% Desktop
- **Phase 7** (Google Docs): ~85% Web, ~15% Desktop
- **Phase 8** (Obsidian): ~90% Web, ~10% Desktop

---

## Phase 6: Watch mode (3-5 days)

### ✅ Can do in Claude Code Web (~70%)

#### 1. FileWatcher implementation (file_watcher.py)
**What**: Core logic for file watching with watchdog library
**Why remote**: All logic, no actual file watching needed
**Tasks**:
- Write `FileWatcher` class structure
- Implement event filtering logic (only .md files, ignore .portals/)
- Write debouncing logic (2 second delay)
- Create event queue management
- Write all unit tests with mocked watchdog events

**Example test approach**:
```python
# Can test in Web with mocks
@pytest.fixture
def mock_watchdog_event():
    return Mock(src_path="/fake/path/file.md", event_type="modified")

def test_file_watcher_filters_md_files(mock_watchdog_event):
    watcher = FileWatcher(base_path="/fake/path")
    assert watcher._should_process(mock_watchdog_event) == True
```

#### 2. NotionPoller implementation (notion_poller.py)
**What**: Polling logic for Notion page changes
**Why remote**: Pure API logic, can use mocked Notion client
**Tasks**:
- Write `NotionPoller` class
- Implement polling loop (every 30 seconds)
- Write change detection logic (compare last_edited_time)
- Queue remote changes
- Unit tests with mocked Notion API responses

**Example test approach**:
```python
# Can test in Web with mocks
@pytest.fixture
def mock_notion_client():
    client = Mock()
    client.pages.retrieve.return_value = {
        "last_edited_time": "2025-11-12T15:30:00.000Z"
    }
    return client

async def test_notion_poller_detects_change(mock_notion_client):
    poller = NotionPoller(notion_client=mock_notion_client)
    changes = await poller.check_for_changes()
    assert len(changes) > 0
```

#### 3. WatchService orchestration (watch_service.py)
**What**: High-level service coordinating watchers and sync
**Why remote**: Business logic only, no real I/O
**Tasks**:
- Write `WatchService` class
- Implement start/stop logic
- Write event processing workflow
- User prompt logic (Y/N/A/Q choices)
- Handle multiple simultaneous changes
- Unit tests with mocked components

#### 4. CLI watch command structure (cli/main.py)
**What**: Click command interface
**Why remote**: Just CLI argument parsing
**Tasks**:
- Add `@cli.command()` for watch
- Define options: `--auto`, `--path`, `--interval`
- Write help text and examples
- Argument validation

#### 5. All unit tests with mocks
**What**: Comprehensive test suite
**Why remote**: Mocked components, no real I/O
**Tasks**:
- `test_file_watcher_*.py` - Mock watchdog events
- `test_notion_poller_*.py` - Mock Notion API
- `test_watch_service_*.py` - Mock both watchers
- Test debouncing logic
- Test filtering logic
- Test prompt flows

### ⚠️ Must do in Claude Code Desktop (~30%)

#### 1. Integration testing with real watchdog
**What**: Test that watchdog actually detects file changes
**Why local**: Need real file system operations
**Tasks**:
- Create test directory
- Write file, verify detection
- Modify file, verify detection
- Delete file, verify detection
- Test debouncing with real timing
- Verify filtering works on real paths

**Example test**:
```python
# MUST run on Desktop
def test_file_watcher_real_filesystem(tmp_path):
    watcher = FileWatcher(base_path=tmp_path)
    watcher.start()

    # Create actual file
    test_file = tmp_path / "test.md"
    test_file.write_text("content")

    # Wait for event
    time.sleep(0.5)

    # Verify watcher detected it
    assert len(watcher.get_pending_changes()) == 1
```

#### 2. End-to-end watch mode testing
**What**: Full watch → detect → prompt → sync workflow
**Why local**: Need real file changes and Notion API
**Tasks**:
- Start watch mode
- Modify local file manually
- Verify prompt appears
- Choose sync option
- Verify file synced to Notion
- Verify metadata updated

#### 3. Performance validation
**What**: Ensure watch mode performs well
**Why local**: Need real system resources
**Tasks**:
- Monitor 50+ files
- Verify CPU usage acceptable
- Test for memory leaks (run 1+ hour)
- Verify debouncing prevents spam

---

## Phase 7: Google Docs pairing (3-5 days)

### ✅ Can do in Claude Code Web (~85%)

#### 1. GoogleDocsAdapter implementation (adapters/gdocs/adapter.py)
**What**: Adapter implementing DocumentAdapter interface
**Why remote**: API client logic, can mock MCP
**Tasks**:
- Write `GoogleDocsAdapter` class
- Implement `read(uri)` method
- Implement `write(uri, doc)` method
- Implement `get_metadata(uri)` method
- Error handling
- Unit tests with mocked MCP responses

**Example**:
```python
# Can develop in Web
class GoogleDocsAdapter(DocumentAdapter):
    def __init__(self, mcp_client):
        self.mcp_client = mcp_client

    async def read(self, uri: str) -> Document:
        # Extract doc ID from uri
        doc_id = self._extract_doc_id(uri)

        # Call MCP (this will be mocked in tests)
        content = await self.mcp_client.get_doc_content(
            document_id=doc_id
        )

        return Document(...)
```

#### 2. MCP client wrapper (adapters/gdocs/mcp_client.py)
**What**: Wrapper around Google Workspace MCP functions
**Why remote**: Just function calls, can mock
**Tasks**:
- Create `MCPClient` class
- Wrap `mcp__google-workspace__get_doc_content`
- Wrap `mcp__google-workspace__modify_doc_text`
- Wrap `mcp__google-workspace__create_doc`
- Error handling and retries
- Unit tests with mocked MCP

#### 3. Format converter (adapters/gdocs/converter.py)
**What**: Markdown ↔ Google Docs format conversion
**Why remote**: Pure string manipulation
**Tasks**:
- Markdown → Google Docs format
- Google Docs format → Markdown
- Handle headings, lists, bold, italic
- Preserve structure
- Unit tests with sample documents

#### 4. PairModeController (controllers/pair_mode.py)
**What**: Business logic for pair mode
**Why remote**: Coordination logic, no I/O
**Tasks**:
- Initialize pair mode
- Create pairs (store in metadata)
- List pairs
- Remove pairs
- Sync paired documents
- Unit tests with mocked adapters

#### 5. CLI commands for pairing
**What**: Click commands for pair, unpair, list
**Why remote**: Just CLI interface
**Tasks**:
- `portals pair <file> <gdoc-uri>`
- `portals unpair <file>`
- `portals list` (show pairs)
- Help text and validation

#### 6. All unit tests
**What**: Comprehensive test coverage
**Why remote**: All components can be mocked
**Tasks**:
- Test adapter with mocked MCP
- Test converter with sample docs
- Test pair mode controller
- Test CLI commands

### ⚠️ Must do in Claude Code Desktop (~15%)

#### 1. Integration with real MCP server
**What**: Verify MCP calls actually work
**Why local**: Need real Google Workspace MCP server
**Tasks**:
- Test reading actual Google Doc
- Test writing to actual Google Doc
- Verify format conversion round-trips
- Test with large documents
- Handle rate limits

**Setup needed**:
```bash
# On Desktop only
export GOOGLE_WORKSPACE_EMAIL="your@email.com"
# MCP server must be running
```

#### 2. End-to-end pair mode testing
**What**: Full workflow with real Google Docs
**Why local**: Need real API and file system
**Tasks**:
- Create pair with real doc
- Modify local file, sync to Docs
- Modify Google Doc, sync to local
- Test conflict resolution
- Verify metadata tracking

---

## Phase 8: Obsidian import (2-3 days)

### ✅ Can do in Claude Code Web (~90%)

#### 1. ObsidianAdapter implementation (adapters/obsidian/adapter.py)
**What**: Read-only adapter for Obsidian notes
**Why remote**: File reading logic, can mock file system
**Tasks**:
- Write `ObsidianAdapter` class (read-only)
- Implement `read(uri)` method
- Parse Obsidian YAML front matter
- Handle attachments references
- Unit tests with mocked files

**Example**:
```python
# Can develop in Web with mocks
class ObsidianAdapter(DocumentAdapter):
    async def read(self, uri: str) -> Document:
        # Extract vault path from uri: obsidian://vault/note.md
        vault_path = self._parse_uri(uri)

        # Read file (will be mocked in tests)
        content = await self._read_file(vault_path)

        # Parse front matter
        metadata = self._parse_frontmatter(content)

        return Document(...)
```

#### 2. Wiki-link parser (adapters/obsidian/wikilink_parser.py)
**What**: Convert Obsidian wiki-links to markdown links
**Why remote**: Pure string parsing, no I/O
**Tasks**:
- Parse `[[Link]]` → `[Link](link.md)`
- Handle `[[Link|Display]]` → `[Display](link.md)`
- Handle `[[Folder/Note]]` paths
- Preserve other markdown
- Extensive unit tests with examples

**Example tests**:
```python
# Can test in Web - pure logic
def test_wikilink_simple():
    input = "See [[Other Note]] for details."
    expected = "See [Other Note](other-note.md) for details."
    assert convert_wikilinks(input) == expected

def test_wikilink_with_display():
    input = "Contact [[john-smith|John Smith]]."
    expected = "Contact [John Smith](john-smith.md)."
    assert convert_wikilinks(input) == expected
```

#### 3. ImportModeController (controllers/import_mode.py)
**What**: Business logic for one-way import
**Why remote**: Coordination only, no real I/O
**Tasks**:
- Import from Obsidian to local
- Import from Obsidian to Notion
- Convert wiki-links
- Handle metadata
- Unit tests with mocked adapters

#### 4. CLI import command
**What**: Click command for import
**Why remote**: Just CLI interface
**Tasks**:
- `portals import <obsidian-uri> --output=<path>`
- `portals import <obsidian-uri> --to=<notion-uri>`
- Validation and help text

#### 5. All unit tests
**What**: Test coverage
**Why remote**: Everything can be mocked
**Tasks**:
- Test adapter with mocked files
- Test wiki-link parser extensively
- Test import controller
- Test CLI command

### ⚠️ Must do in Claude Code Desktop (~10%)

#### 1. Integration with real Obsidian vault
**What**: Test reading actual Obsidian notes
**Why local**: Need real vault directory
**Tasks**:
- Point to actual Obsidian vault
- Read real notes with wiki-links
- Verify conversion works correctly
- Test with various link formats
- Handle edge cases

**Setup needed**:
```bash
# On Desktop only - need real Obsidian vault
export OBSIDIAN_VAULT_PATH="/Users/you/Obsidian/YourVault"
```

#### 2. End-to-end import testing
**What**: Full import workflow
**Why local**: Need real files and Notion API
**Tasks**:
- Import to local file
- Import to Notion page
- Verify wiki-links converted
- Verify attachments handled
- Test with real notes

---

## Development workflow

### Recommended approach

**Phase 6 workflow**:
1. **Week 1 in Web** (70% of work):
   - Implement FileWatcher, NotionPoller, WatchService
   - Write all unit tests with mocks
   - Implement CLI command
   - Get to 85%+ test coverage

2. **Day 1-2 in Desktop** (30% of work):
   - Run integration tests with real watchdog
   - Test end-to-end watch mode
   - Validate performance
   - Fix any issues discovered

**Phase 7 workflow**:
1. **Week 1 in Web** (85% of work):
   - Implement GoogleDocsAdapter with mocked MCP
   - Write format converter
   - Implement PairModeController
   - Add CLI commands
   - Unit tests with mocks

2. **Day 1 in Desktop** (15% of work):
   - Test with real Google Workspace MCP
   - End-to-end pair mode testing
   - Fix integration issues

**Phase 8 workflow**:
1. **3-4 days in Web** (90% of work):
   - Implement ObsidianAdapter
   - Write wiki-link parser with extensive tests
   - Implement ImportModeController
   - CLI command

2. **Half day in Desktop** (10% of work):
   - Test with real Obsidian vault
   - End-to-end import testing
   - Edge case handling

### Merging strategy

**After Web development**:
```bash
# On Desktop
git fetch origin
git checkout -b phase-6-web-work
git merge origin/claude/phase-6-branch
git checkout main
git merge phase-6-web-work
```

**After Desktop testing**:
```bash
# Continue on same branch
git add tests/integration/
git commit -m "test: Add Phase 6 integration tests"
git push origin main
```

---

## What can be mocked vs what needs real

### Can be mocked in Web

**File system operations**:
```python
# Mock file reading
@pytest.fixture
def mock_file_system(tmp_path, monkeypatch):
    def mock_read(path):
        return "# Mocked content"
    monkeypatch.setattr("builtins.open", mock_read)
```

**Watchdog events**:
```python
# Mock watchdog
@pytest.fixture
def mock_watchdog_event():
    return Mock(
        src_path="/fake/path.md",
        event_type="modified",
        is_directory=False
    )
```

**Notion API**:
```python
# Mock Notion client
@pytest.fixture
def mock_notion_client():
    client = Mock()
    client.pages.retrieve.return_value = {"id": "page-123"}
    return client
```

**MCP functions**:
```python
# Mock MCP calls
@pytest.fixture
def mock_mcp_client():
    client = Mock()
    client.get_doc_content.return_value = "Doc content"
    return client
```

### Needs real in Desktop

**Actual file watching**:
- Must create real files and verify watchdog detects them
- Timing-dependent (debouncing)
- OS-specific behavior

**Real API integration**:
- Notion API rate limits
- Google Workspace MCP authentication
- Real response formats
- Error scenarios

**Performance characteristics**:
- Memory usage over time
- CPU usage with many files
- Network latency
- Concurrent operations

---

## Checklist for each phase

### Before starting in Web

- [ ] Read implementation plan for the phase
- [ ] Understand acceptance criteria
- [ ] Plan what can be mocked
- [ ] Set up test fixtures

### During Web development

- [ ] Implement core classes
- [ ] Write unit tests with mocks
- [ ] Achieve 85%+ test coverage
- [ ] Add CLI commands
- [ ] Update documentation

### Moving to Desktop

- [ ] Pull latest from Web branch
- [ ] Review implemented code
- [ ] Set up real test environment (APIs, files, etc.)
- [ ] Write integration tests
- [ ] Run end-to-end tests
- [ ] Fix issues discovered
- [ ] Validate performance

### After Desktop testing

- [ ] Commit integration tests
- [ ] Update PROGRESS.md
- [ ] Push to GitHub
- [ ] Mark phase complete

---

## Time estimates

| Phase | Web Time | Desktop Time | Total | Web % |
|-------|----------|--------------|-------|-------|
| Phase 6 | 2-3 days | 1-2 days | 3-5 days | 70% |
| Phase 7 | 2.5-4 days | 0.5-1 day | 3-5 days | 85% |
| Phase 8 | 1.5-2.5 days | 0.5 day | 2-3 days | 90% |
| **Total** | **6-9.5 days** | **2-3.5 days** | **8-13 days** | **75%** |

**Key insight**: ~75% of remaining work can be done remotely in Claude Code Web!

---

## Common pitfalls to avoid

### In Web development

❌ **Don't**: Try to test real file system operations
✅ **Do**: Use mocks and temporary paths

❌ **Don't**: Skip tests because "I'll test later on Desktop"
✅ **Do**: Write comprehensive unit tests with mocks now

❌ **Don't**: Hard-code paths or credentials
✅ **Do**: Use environment variables and configuration

### In Desktop testing

❌ **Don't**: Skip integration tests because "unit tests passed"
✅ **Do**: Test with real APIs and file systems

❌ **Don't**: Test with production Notion workspace
✅ **Do**: Use test workspace or pages

❌ **Don't**: Commit secrets or local paths
✅ **Do**: Use .env and relative paths

---

## Success criteria

### For Web development phase
- [ ] All core classes implemented
- [ ] 85%+ unit test coverage with mocks
- [ ] All tests pass locally (with mocks)
- [ ] Code follows project style (ruff, mypy)
- [ ] Documentation updated
- [ ] Ready for Desktop integration testing

### For Desktop testing phase
- [ ] Integration tests written and passing
- [ ] End-to-end workflows tested manually
- [ ] Performance validated (CPU, memory)
- [ ] Edge cases handled
- [ ] No regressions in existing features
- [ ] Phase marked complete in PROGRESS.md

---

## Summary

**Key takeaway**: You can do 70-90% of Phases 6-8 in Claude Code Web by:
1. Writing all logic and algorithms
2. Creating comprehensive unit tests with mocked dependencies
3. Implementing CLI commands
4. Updating documentation

Reserve Claude Code Desktop for:
1. Integration tests with real file system/APIs
2. End-to-end workflow validation
3. Performance testing
4. Edge case discovery

This approach maximizes your flexibility to work remotely while ensuring quality through proper testing on Desktop when needed.
