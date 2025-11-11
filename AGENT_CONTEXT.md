# Portals - Agent context

## Project overview

**Official name**: Portals
**CLI command**: `docsync`

Multi-platform document synchronization tool that keeps local markdown files in sync with Notion, Google Docs, and Obsidian.

**Primary use case**: Mirror the entire `~/Documents/Claude Code/` directory to the Portals team space in Notion for work and business documentation.

**Created**: 2025-11-11
**Owner**: Roman Siepelmeyer
**Status**: Planning phase
**Notion team space**: Portals

---

## Core philosophy

### Database separation strategy

**Notion** = Work and business
- Claude Code projects
- Work documentation
- Structured, collaborative content
- Primary sync target

**Obsidian** = Personal knowledge base
- Self-reflection
- Book notes
- Personal insights
- Read-only import source

**No bidirectional sync needed** between Notion and Obsidian - they serve different purposes.

### Sync philosophy

- **Semi-automatic**: Prompt before syncing, don't sync silently
- **Manual conflict resolution**: Show diffs, let user decide
- **Local-first**: Local files are source of truth
- **Transparent**: Clear status and error messages

---

## Directory structure

```
docsync/
├── .gitignore                    # Git ignore rules
├── .env.example                  # Environment variables template
├── README.md                     # Project overview
├── AGENT_CONTEXT.md              # This file - project navigation
├── PROGRESS.md                   # Development progress tracker (for agents)
├── 01-context/                   # Strategic documents
│   ├── 2025-11-11_IMPLEMENTATION_PLAN.md  # Complete implementation plan
│   ├── 2025-11-11_GIT_GUIDE.md           # Git best practices
│   └── 2025-11-11_NOTION_STRUCTURE.md    # Notion structure decisions
├── 02-research/                  # Research and analysis
├── 03-implementation/            # Work in progress during development
├── 04-agents/                    # Agent configurations
├── examples/                     # Usage examples
└── tests/                        # Test files (when built)
```

---

## Key documents

### Agent continuity

**`PROGRESS.md`** (start here for continuing work!)
- Current phase status and completion percentage
- Completed tasks with commit references
- Remaining tasks for current phase
- Quick status for agents picking up work
- Next session checklist
- **Read this first when continuing development!**

### Planning and architecture

**`01-context/2025-11-11_IMPLEMENTATION_PLAN.md`** (primary document)
- Complete system architecture
- Implementation phases (0-8)
- Technical specifications
- Testing strategy
- Timeline estimates
- **Read this for initial understanding!**

**`01-context/2025-11-11_NOTION_STRUCTURE.md`**
- Notion team space structure (Portals)
- Folder → page mapping decisions
- Nested pages hierarchy
- Implementation implications
- **Read before Phase 2**

**`01-context/2025-11-11_GIT_GUIDE.md`**
- Git fundamentals for beginners
- What to commit vs what to ignore
- Best practices from senior engineers
- Common pitfalls and solutions
- Daily workflow guide
- **Essential for version control**

---

## Operating modes

DocSync has three distinct operating modes:

### 1. Mirror mode (Notion) - PRIMARY
Entire directory ↔ Notion workspace

**Use case**: Keep all Claude Code projects synced with Notion
**Characteristics**: Automatic pairing, hierarchical sync, always-on watch mode

### 2. Pair mode (Google Docs) - OCCASIONAL
Selective individual document pairing

**Use case**: Collaborate on specific documents via Google Docs
**Characteristics**: Manual pairing, on-demand sync, 5-10 documents max

### 3. Import mode (Obsidian) - ONE-WAY
Import from Obsidian vault for inspiration

**Use case**: Pull personal insights into work context
**Characteristics**: Read-only, no tracking, format conversion

---

## Implementation phases

**Current phase**: Phase 0 - Not started

### Phase breakdown

| Phase | Description | Duration | Priority |
|-------|-------------|----------|----------|
| 0 | Foundation and project setup | 3-5 days | Required |
| 1 | Local file operations | 3-5 days | Required |
| 2 | Notion adapter | 5-7 days | Required |
| 3 | Mirror mode initialization | 3-4 days | Required |
| 4 | Bidirectional sync | 5-7 days | Required |
| 5 | Conflict resolution | 4-5 days | Required |
| 6 | Watch mode | 3-5 days | Required |
| 7 | Google Docs pairing | 3-5 days | Optional |
| 8 | Obsidian import | 2-3 days | Optional |

**MVP** (Phases 0-6): ~21-33 days
**Full implementation**: ~31-50 days

---

## Technology stack

**Core**:
- Python 3.11+ with asyncio
- uv for dependency management
- Click for CLI
- Rich for terminal UI

**Key libraries**:
- `notion-client` - Notion API
- `watchdog` - File system monitoring
- `structlog` - Structured logging
- `pytest` - Testing

**Platform integrations**:
- Notion API (official SDK)
- Google Workspace MCP (existing server)
- Obsidian vault (read-only file access)

---

## Project source code structure (when built)

```
docsync/
├── pyproject.toml
├── docsync/                      # Main package
│   ├── cli/                      # CLI commands
│   ├── controllers/              # Mode controllers
│   ├── services/                 # Business logic
│   ├── core/                     # Sync engine
│   ├── adapters/                 # Platform integrations
│   │   ├── local.py
│   │   ├── notion/
│   │   ├── gdocs/
│   │   └── obsidian/
│   ├── watcher/                  # File watching
│   └── utils/                    # Utilities
└── tests/                        # Test suite
```

---

## How to use this project

### For agents continuing development

1. **Start with `PROGRESS.md`** - See current phase, completed tasks, and what's next
2. Review "Remaining Phase X tasks" section in PROGRESS.md
3. Check recent commits to understand latest changes
4. Continue from the next pending task
5. Update PROGRESS.md as you complete tasks

### For initial project understanding

1. Read `01-context/2025-11-11_IMPLEMENTATION_PLAN.md` thoroughly
2. Understand the three operating modes
3. Follow implementation phases sequentially
4. Each phase has clear acceptance criteria

### For understanding the codebase

1. Start with `docsync/core/models.py` - data structures
2. Then `docsync/adapters/base.py` - adapter interface
3. Then `docsync/core/sync_engine.py` - sync algorithm
4. Mode controllers tie everything together

### For using the tool (once built)

```bash
# Initialize mirror mode for Notion sync
cd ~/Documents/Claude\ Code/
docsync init notion-mirror --database=<database-id>
docsync watch

# Create selective Google Docs pair
docsync pair report.md gdoc://abc123
docsync sync report.md

# Import from Obsidian
docsync import obsidian://vault/note.md --to=notion://page-id
```

---

## Key architectural decisions

### 1. Why three modes instead of one generic tool?

Different workflows have fundamentally different characteristics:
- Mirror mode: Automatic, hierarchical, always-on
- Pair mode: Manual, selective, on-demand
- Import mode: One-way, no tracking

### 2. Why Notion as primary over Obsidian?

- Better API support (official SDK)
- Better collaboration features
- Web-based access
- Database and properties
- Aligns with user's natural usage (work/business)

### 3. Why semi-automatic instead of fully automatic?

- User wants control over syncs
- Manual conflict resolution is safer
- Prompts provide visibility
- Prevents accidental overwrites

### 4. Why local-first architecture?

- Local files are source of truth
- Works offline
- Git-friendly
- No vendor lock-in

### 5. Why 3-way merge?

- Most reliable conflict detection
- Tracks last synced state (base)
- Can differentiate scenarios accurately
- Industry standard (Git uses this)

---

## Open questions

Questions to resolve during implementation:

1. **Notion structure**: Database vs nested pages for folder hierarchy?
2. **Folder representation**: How to represent empty folders in Notion?
3. **Attachment handling**: Upload to Notion or keep local references?
4. **Metadata location**: Store Notion page ID in YAML front matter?
5. **Git integration**: Should `.docsync/` be committed?

---

## Success criteria

### Functional
- Successfully sync 50+ files in mirror mode
- Detect and resolve conflicts correctly
- Watch mode runs reliably for 24+ hours
- Zero data loss or corruption

### Performance
- Initial sync of 50 files in < 2 minutes
- Individual file sync in < 5 seconds
- Change detection in < 100ms

### Usability
- New user can set up in < 5 minutes
- Clear error messages
- Intuitive conflict resolution

---

## Current status

**Phase**: Planning complete
**Next step**: Begin Phase 0 implementation

### To start development

1. Review and approve implementation plan
2. Resolve open questions
3. Set up Python project with uv
4. Create base classes and project structure
5. Begin Phase 0 tasks

---

## Related projects

**Similar tools** (for reference):
- `notion2md` - One-way Notion to Markdown export
- `obsidian-notion-sync` - Obsidian plugin for Notion sync
- `md2notion` - One-way Markdown to Notion import

**Our differentiation**:
- Bidirectional sync
- Multiple modes for different workflows
- Directory-level mirroring
- Semi-automatic with prompts
- Extensible adapter architecture

---

## Future enhancements (post-MVP)

Ideas for later versions:
1. Web dashboard for monitoring sync status
2. AI-powered conflict resolution (using Claude)
3. Additional platforms (Confluence, Evernote)
4. Team sync with shared configuration
5. Version history and rollback
6. Git integration (auto-commit on sync)
7. Mobile companion app

---

## Getting help

### Documentation locations
- **Implementation plan**: `01-context/2025-11-11_IMPLEMENTATION_PLAN.md`
- **Architecture diagrams**: In implementation plan
- **API references**: See implementation plan appendices

### For questions
- Review implementation plan first
- Check open questions section
- Consult architecture diagrams

### For development
- Follow phase sequence
- Each phase has acceptance criteria
- Tests define behavior
- Keep this document updated

---

## Maintenance notes

### When to update this document

- Starting a new phase
- Making architectural changes
- Resolving open questions
- Adding new modes or features
- Changing project structure

### Document history

- **2025-11-11**: Initial creation, planning phase complete

---

**Quick start for new agents**: Read `PROGRESS.md` (current status) → Read `01-context/2025-11-11_IMPLEMENTATION_PLAN.md` (full plan) → Continue from next task

**Quick start for initial setup**: Read `01-context/2025-11-11_IMPLEMENTATION_PLAN.md` → Understand three modes → Begin Phase 0
