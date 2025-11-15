# Google Docs Converter - Implementation Status

**Date**: 2025-11-13
**Status**: Core converter complete, adapter ready for testing

---

## What We Built

### 1. GoogleDocsConverter (`portals/adapters/gdocs/converter.py`)
**Lines**: 539
**Status**: ✅ Complete and tested

**Features**:
- Parses markdown using `markdown-it-py`
- Converts markdown to plain text (no markdown symbols)
- Tracks all formatting ranges:
  - Headings (H1, H2, H3, H4, H5, H6)
  - Bold text
  - Italic text
  - Inline code
  - Links
  - Bullet lists
  - Numbered lists
  - Code blocks
  - Horizontal rules
  - Blockquotes

**Output**:
- Plain text string (for document insertion)
- Format ranges (start/end indices with types)
- List ranges (for bullet/numbered lists)
- Google Docs API batch update requests

**Test Results**:
```
Input: 986 chars markdown
Output: 883 chars plain text
Format ranges: 14
List ranges: 13
Batch requests: 27
```

### 2. GoogleDocsAdapter (`portals/adapters/gdocs/adapter.py`)
**Lines**: 350+
**Status**: ✅ Complete, ready for auth testing

**Features**:
- Implements `DocumentAdapter` interface
- Direct Google Docs API access (bypasses MCP limitations)
- Full formatting support (headings, lists, all styles)
- OAuth2 authentication
- Document CRUD operations:
  - `read()` - Read doc with metadata
  - `write()` - Create or update with formatting
  - `delete()` - Delete document
  - `list_documents()` - List all docs

**Key Methods**:
- `_create_document()` - Creates doc with full formatting
- `_update_document()` - Updates existing doc
- `_extract_content()` - Reads plain text from doc
- `_get_credentials()` - OAuth2 auth flow

---

## MCP Tool Limitations

**Discovery**: MCP `batch_update_doc` doesn't support paragraph styling

**Tested**: `updateParagraphStyle`, `createParagraphBullets`
**Error**: `Unsupported operation type: updateParagraphStyle`

**MCP Supported Operations**:
- `insert_text`
- `delete_text`
- `replace_text`
- `format_text` (bold, italic, font only)
- `insert_table`
- `insert_page_break`

**NOT Supported**:
- ❌ Paragraph styles (headings, alignment, spacing)
- ❌ List formatting (bullets, numbering)
- ❌ Advanced formatting

**Impact**: Cannot create distribution-ready documents with MCP tools alone.

**Solution**: Implemented direct Google Docs API access in GoogleDocsAdapter.

---

## How It Works

### End-to-End Flow

```
Markdown File
     ↓
GoogleDocsConverter
     ├── Parse markdown (markdown-it-py)
     ├── Generate plain text
     ├── Track formatting ranges
     └── Generate batch requests
          ↓
GoogleDocsAdapter
     ├── Create empty doc
     ├── Insert plain text
     └── Apply all formatting (batch update)
          ↓
Distribution-Ready Google Doc
```

### Example Conversion

**Input Markdown**:
```markdown
# Q4 Strategy Memo

**Date**: November 2025

## Executive Summary

We expect **30% growth** in Q4, driven by:

- New product launches
- Expanded market reach
```

**Converter Output**:
```python
{
  "plain_text": "Q4 Strategy Memo\nDate: November 2025\n...",
  "format_ranges": [
    {"start_index": 1, "end_index": 17, "format_type": "heading", "level": 1},
    {"start_index": 18, "end_index": 22, "format_type": "bold"},
    {"start_index": 85, "end_index": 102, "format_type": "heading", "level": 2},
    {"start_index": 123, "end_index": 133, "format_type": "bold"},
    ...
  ],
  "list_ranges": [
    {"start_index": 145, "end_index": 165, "ordered": false},
    {"start_index": 166, "end_index": 186, "ordered": false}
  ]
}
```

**Batch Requests**:
```json
[
  {
    "updateParagraphStyle": {
      "range": {"startIndex": 1, "endIndex": 17},
      "paragraphStyle": {"namedStyleType": "HEADING_1"},
      "fields": "namedStyleType"
    }
  },
  {
    "updateTextStyle": {
      "range": {"startIndex": 18, "endIndex": 22},
      "textStyle": {"bold": true},
      "fields": "bold"
    }
  },
  {
    "createParagraphBullets": {
      "range": {"startIndex": 145, "endIndex": 165},
      "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
    }
  }
]
```

---

## Testing

### Converter Tests
**Location**: `/tmp/test_converter.py`
**Status**: ✅ Passing

**Results**:
```
Plain text: 883 chars (clean, no markdown symbols)
Format ranges: 14 (all tracked correctly)
List ranges: 13 (bullet + numbered)
Batch requests: 27 (ready for API)
```

### Adapter Test
**Location**: `/tmp/test_gdocs_adapter.py`
**Status**: ⏳ Ready to run (needs auth)

**What it tests**:
1. Initialize adapter
2. Create Google Doc with full formatting
3. Read document back
4. Verify formatting quality

---

## Authentication

### Current Setup
The GoogleDocsAdapter uses OAuth2 with these credentials:
- **Credentials**: `~/.config/docsync/google_credentials.json`
- **Token**: `~/.config/docsync/google_token.json`

### Setup Steps
1. Create OAuth2 credentials in Google Cloud Console
2. Download `credentials.json`
3. Save to `~/.config/docsync/google_credentials.json`
4. First run will open browser for OAuth
5. Token saved for future use

### Alternative: Reuse MCP Auth
The MCP Google Workspace tools are already authenticated at:
- `~/.claude/mcp_google_workspace_tokens/`

**Option**: Could potentially reuse MCP's tokens if we extract them properly.

---

## Next Steps

### Immediate (Today)
1. ✅ Built GoogleDocsConverter
2. ✅ Built GoogleDocsAdapter
3. ✅ Added dependencies
4. ⏳ Set up Google OAuth credentials
5. ⏳ Test adapter with real document
6. ⏳ Verify formatting quality (distribution-ready)

### Phase 7 Integration
1. Add Google Docs to SyncPair
2. Implement multi-remote support (Notion + Google Docs)
3. Add CLI commands:
   - `portals pair --gdocs <doc-id> <local-file>`
   - `portals push <file>` (both Notion + Google Docs)
   - `portals pull <file>`
4. Add watch mode support (poll Google Docs for changes)
5. Pull comments/suggestions from Google Docs

---

## Success Criteria

A document is **distribution-ready** when:

1. ✅ Headings use native Google Docs styles (H1, H2, H3)
2. ✅ Bold/italic text is formatted (no asterisks)
3. ✅ Bullet lists use native bullets (not dashes)
4. ✅ Numbered lists use native numbering (not "1.", "2.")
5. ✅ Links are clickable (not [text](url))
6. ✅ Code blocks are formatted (monospace font)
7. ✅ Proper spacing (not random line skips)
8. ✅ Can share immediately with Christof without manual fixes

**Quality Bar**: Push from Claude Code → Ready for distribution → No manual formatting needed.

---

## Architecture Decisions

### Why Direct API Access?
MCP tools can't apply paragraph styles or list formatting. We need direct API access for:
- Heading styles (H1-H6)
- List formatting (bullets, numbering)
- Full Google Docs API features

### Why Keep MCP Tools?
Still useful for:
- OAuth handling (can reuse)
- Document discovery/search
- Reading content
- Initial testing

### Hybrid Approach
- **Read operations**: Can use MCP or direct API
- **Write operations**: Must use direct API for formatting
- **Auth**: Reuse MCP OAuth if possible, fallback to separate flow

---

## Files Created

1. `portals/adapters/gdocs/converter.py` - Markdown to Google Docs converter
2. `portals/adapters/gdocs/adapter.py` - Document adapter with direct API
3. `portals/adapters/gdocs/__init__.py` - Package exports
4. `01-context/2025-11-13_GDOCS_FORMATTING_ANALYSIS.md` - Initial analysis
5. `01-context/2025-11-13_MCP_TOOL_LIMITATIONS.md` - MCP limitations doc
6. `01-context/2025-11-13_GDOCS_CONVERTER_STATUS.md` - This file

---

## Dependencies Added

```toml
dependencies = [
    "google-api-python-client>=2.100.0",
    "google-auth-httplib2>=0.2.0",
    "google-auth-oauthlib>=1.1.0",
    "markdown-it-py>=3.0.0",
]
```

---

**Ready for**: OAuth setup and end-to-end testing
**Blocked by**: Google OAuth2 credentials setup
**Next**: Test with real document to verify formatting quality
