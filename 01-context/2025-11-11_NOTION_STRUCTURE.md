# Notion structure decisions for Portals

**Created**: 2025-11-11
**Project**: Portals (DocSync)
**Notion team space**: Portals

---

## Decisions made

### 1. Team space name: Portals ✅

**Decision**: Use "Portals" team space in Notion

**Structure**:
- Team space name: **Portals**
- All synced content goes into this team space
- Organized as nested pages (not database)

### 2. Folder mapping: Nested pages ✅

**Decision**: Use nested pages (not Notion databases)

**Mapping**:
```
Local folder structure:

~/Documents/Claude Code/
├── project-a/
│   ├── 01-context/
│   │   ├── architecture.md
│   │   └── planning.md
│   ├── 02-research/
│   │   └── findings.md
│   └── AGENT_CONTEXT.md
└── project-b/
    └── notes.md

→ Becomes in Notion:

Portals (Team Space)
├── 📄 project-a (page)
│   ├── 📄 01-context (child page)
│   │   ├── 📄 Architecture (child page)
│   │   └── 📄 Planning (child page)
│   ├── 📄 02-research (child page)
│   │   └── 📄 Findings (child page)
│   └── 📄 Agent Context (child page)
└── 📄 project-b (page)
    └── 📄 Notes (child page)
```

**Key principles**:
1. **Folders → Pages**: Each folder becomes a Notion page
2. **Subfolders → Child pages**: Nested folders become nested pages
3. **Markdown files → Child pages**: `.md` files become pages under their folder
4. **Hierarchy preserved**: Full directory structure maintained in Notion

### 3. Page titles

**Decision**: Use human-readable titles

**Conversion**:
```
File: architecture.md        → Page title: "Architecture"
File: 01-context/            → Page title: "01-context"
File: AGENT_CONTEXT.md       → Page title: "Agent Context"
File: 2025-11-11_PLAN.md     → Page title: "2025-11-11 Plan"
```

**Rules**:
- Remove `.md` extension
- Keep underscores and dashes as-is (for now)
- Title case in Notion (configurable later)

### 4. Empty folders

**Decision**: Create empty parent pages

**Example**:
```
Local:
project-a/
├── empty-folder/
└── notes.md

Notion:
📄 project-a
  ├── 📄 empty-folder (empty page, just a container)
  └── 📄 Notes
```

**Why?**
- Preserves structure
- Allows adding content later
- Clear hierarchy

### 5. Attachments and images

**Decision**: Upload to Notion (Phase 2 enhancement)

**For MVP**:
- Markdown files only
- References to images broken (Phase 1)
- Later: Upload images to Notion (Phase 2)

**Example**:
```markdown
# Before sync
![Diagram](./images/architecture.png)

# After sync (MVP)
![Diagram](./images/architecture.png)  # Broken link in Notion

# After sync (Phase 2)
![Diagram](https://notion.so/uploaded/image.png)  # Working link
```

---

## Notion API structure

### How to represent in code

**URI format**:
```
notion://portals/project-a/01-context/architecture
```

**Metadata tracking**:
```json
{
  "teamspace_id": "portals-team-space-id",
  "hierarchy": {
    "project-a": {
      "notion_page_id": "page-abc123",
      "children": {
        "01-context": {
          "notion_page_id": "page-def456",
          "children": {
            "architecture.md": {
              "notion_page_id": "page-ghi789"
            }
          }
        }
      }
    }
  }
}
```

### Creating pages with parent relationships

**Notion API call**:
```python
from notion_client import Client

client = Client(auth=os.getenv("NOTION_API_KEY"))

# Create folder page (parent)
parent_page = client.pages.create(
    parent={"type": "workspace", "workspace": True},
    properties={
        "title": [{"text": {"content": "project-a"}}]
    }
)

# Create child page
child_page = client.pages.create(
    parent={"type": "page_id", "page_id": parent_page["id"]},
    properties={
        "title": [{"text": {"content": "Architecture"}}]
    },
    children=[
        # Notion blocks (converted from markdown)
    ]
)
```

---

## Implementation implications

### Phase 2: Notion adapter

**Must implement**:
1. ✅ Create pages with parent relationships
2. ✅ List child pages
3. ✅ Create nested hierarchy from directory structure
4. ✅ Convert markdown to Notion blocks
5. ✅ Convert Notion blocks to markdown

**Notion API methods needed**:
- `pages.create()` with `parent` parameter
- `blocks.children.list()` to get child pages
- `blocks.children.append()` to add content
- `pages.retrieve()` to get page info

### Phase 3: Mirror mode initialization

**Workflow**:
1. Scan local directory recursively
2. Build hierarchy tree
3. Create Notion pages from root to leaves
4. For each folder:
   - Create parent page
   - Create child pages for subfolders
   - Create child pages for markdown files
5. Upload markdown content as blocks
6. Save mapping (local path → Notion page ID)

**Example code structure**:
```python
class HierarchyMapper:
    def build_tree(self, root_path: Path) -> DirectoryNode:
        """Build tree from local directory"""
        pass

    async def create_notion_hierarchy(
        self,
        tree: DirectoryNode,
        parent_id: Optional[str] = None
    ) -> dict[str, str]:
        """Create Notion pages, return path → page_id mapping"""
        pass
```

---

## CLI command updates

### Init command

**Old**:
```bash
docsync init notion-mirror --database=<database-id>
```

**New**:
```bash
docsync init notion-mirror --teamspace=portals

# Or with explicit team space ID
docsync init notion-mirror --teamspace-id=<id>
```

### Configuration

**Store in `.portals/config.json`**:
```json
{
  "mode": "notion-mirror",
  "notion": {
    "teamspace_name": "Portals",
    "teamspace_id": "abc123...",
    "api_key": "${NOTION_API_KEY}"
  }
}
```

---

## Benefits of nested pages approach

### vs Notion database

**Nested pages** (our choice):
- ✅ Simpler hierarchy (just parent-child)
- ✅ Direct representation of folders
- ✅ Easy to navigate in Notion
- ✅ Works with existing Notion workflows
- ✅ No schema to manage

**Notion database**:
- ❌ More complex (needs database + pages)
- ❌ Harder to represent nested folders
- ✅ Better for filtering/sorting
- ✅ Properties and metadata

**Decision**: Start with nested pages, can migrate to database later if needed.

---

## Example scenarios

### Scenario 1: New project

**Action**: Create new project locally
```bash
mkdir ~/Documents/Claude\ Code/new-project
touch ~/Documents/Claude\ Code/new-project/README.md
```

**Sync result**: In watch mode, DocSync detects new folder and file
```bash
[Watch] New folder detected: new-project/
        Create Notion page?
        [Y] Yes  [N] No

        Choice: y

[Watch] ✓ Created page "new-project" in Portals
[Watch] ✓ Created child page "README"
```

### Scenario 2: Nested folder structure

**Action**: Create nested folders locally
```bash
mkdir -p ~/Documents/Claude\ Code/project/docs/api
touch ~/Documents/Claude\ Code/project/docs/api/endpoints.md
```

**Sync result**: Creates hierarchy in Notion
```
Portals
└── 📄 project
    └── 📄 docs
        └── 📄 api
            └── 📄 Endpoints
```

### Scenario 3: Rename folder

**Action**: Rename folder locally
```bash
mv ~/Documents/Claude\ Code/old-name ~/Documents/Claude\ Code/new-name
```

**Sync result**: Updates Notion page title
```bash
[Watch] Folder renamed: old-name → new-name
        Update Notion page title?
        [Y] Yes  [N] No

        Choice: y

[Watch] ✓ Updated page title "old-name" → "new-name"
```

### Scenario 4: Delete folder

**Action**: Delete folder locally
```bash
rm -rf ~/Documents/Claude\ Code/old-project
```

**Sync result**: Archives Notion page
```bash
[Watch] Folder deleted: old-project/
        Archive Notion page? (not permanent delete)
        [Y] Yes  [N] No

        Choice: y

[Watch] ✓ Archived page "old-project" in Notion
```

---

## Open questions (for later)

### Q1: Should we preserve folder order?

Notion doesn't have explicit ordering for pages. Options:
- Let Notion sort alphabetically
- Use emoji prefixes for ordering (📁 01-context, 📁 02-research)
- Store order in metadata

**Decision**: Later - let Notion handle it for now

### Q2: How to handle special characters in names?

Some characters aren't allowed in Notion page titles:
- `/` (path separator)
- `\` (backslash)
- `:` (colon in some cases)

**Decision**: Replace with safe alternatives (sanitize)

### Q3: Maximum nesting depth?

Notion supports deep nesting. Should we limit it?

**Decision**: No limit for now, let Notion handle it

---

## Summary

✅ **Team space**: Portals
✅ **Structure**: Nested pages (not databases)
✅ **Folders**: Become parent pages
✅ **Files**: Become child pages
✅ **Hierarchy**: Fully preserved
✅ **Git**: Don't commit `.portals/` (local state)

**Next**: Implement in Phase 2 and Phase 3!
