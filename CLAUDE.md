# Portals - project instructions

## What this project is

Portals is the multi-project Google Workspace integration layer. It contains adapters for Google Docs, Gmail, and other services, plus CLI scripts that the md2gdoc skill and other skills invoke.

## Build protocol for external-output adapters

These rules apply whenever modifying code in `portals/adapters/` - anything that generates output for an external system (Google Docs API, Sheets API, Gmail API) where Claude cannot see the rendered result.

### One feature at a time

Never batch multiple features into a single implementation pass. The workflow is:

1. Implement ONE feature or fix
2. Push to the external system (create/update a test doc)
3. User visually verifies the result
4. Confirm it works before starting the next change

If a plan has 8 steps, execute them as 8 separate implement-verify cycles, not one large batch. Bugs compound when stacked.

### Side effect discipline

Before applying any formatting change, ask: **what else does this touch?**

- **Text-level styles** (fontSize, foregroundColor) propagate across table insertion boundaries. Never apply text styles to spacer/placeholder paragraphs.
- **Paragraph-level styles** (spaceAbove, spaceBelow) are safe for spacers - they don't bleed into adjacent elements.
- **Format range boundaries** must never split a multi-unit character (emoji surrogate pairs in UTF-16). When adjusting offset calculations, verify boundary correctness on both sides.
- **Named styles** (HEADING_3, SUBTITLE) are safer than manual font/size/color overrides. They respect the document theme and don't create unexpected interactions.

### Test before push

When modifying the converter, run the conversion on the test markdown and inspect the batch requests programmatically before pushing to Google Docs:

- Check for overlapping format ranges
- Check for text styles applied to empty/spacer paragraphs
- Check that format range boundaries don't land inside multi-byte characters
- Print section classifications from the pre-scan model to verify detection logic

### Context preservation across long sessions

The Google Docs converter is a ~1800-line file with subtle interactions between the pre-scan model, token processing, and batch request generation. When a session compacts:

- The summary captures WHAT was done but loses the intuition about WHY approaches failed
- Re-read the relevant code sections before making changes - don't rely on summary alone
- The inline code comments (especially the UTF-16 offset explanation) exist because these bugs recurred. Read them.

### What Claude can't do

Claude cannot see the rendered Google Doc. The user is the visual verification layer. Keep the feedback loop tight:

- Push early, push often
- One change per push when debugging visual issues
- Ask the user to screenshot specific areas rather than making assumptions about rendering

## Key files

- `portals/adapters/gdocs/converter.py` - Markdown-to-Google-Docs converter (two-pass: pre-scan + render)
- `scripts/docs_operations.py` - CLI for create/read/update Google Docs (--project flag for multi-account)
- `scripts/gmail_operations.py` - CLI for Gmail operations (--project flag)
- `scripts/slides_operations.py` - Google Slides operations with three-tier editing (see below)

## Google Slides three-tier editing model

`scripts/slides_operations.py` supports three tiers of editing precision:

### Tier 1: Surgical element-level edits (primary)

Use `--inspect` to see every element on a slide (objectId, type, position, text), then `--patch` to modify individual elements without touching anything else. Like a human clicking on a text box and editing it.

**Workflow**: inspect -> identify objectId -> patch with targeted operations

Operations supported: `update_text`, `style_text`, `style_para`, `move` (reposition/resize), `fill` (background color), `delete`

### Tier 2: Single-slide rebuild (fallback)

Use `--rebuild-slide` when the layout itself changes (e.g., bullets -> matrix table). Clears one slide and rebuilds it. Other slides untouched. Comments survive.

### Tier 3: Full rebuild / selective rebuild

`--update-from-json` with optional `--slides "0,3,5"` for selective rebuild. Without `--slides`, full rebuild (existing behavior).

### Comment safety

Google Slides comments live at the presentation level via Drive API. They are NOT attached to pageElements. All three tiers preserve comments.

## Script CLI patterns

All scripts use `uv run` from the portals root:
```
cd ~/Documents/Claude\ Code/portals && uv run python scripts/docs_operations.py [args]
```

Multi-account: `--project anaro-labs` (default) or `--project estate-mate`
