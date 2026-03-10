---
created: 2026-03-10
version: v1.0
---

# Memo formatting stress test - results dashboard

## Test artifacts

| Artifact | Doc ID | Pages |
|----------|--------|-------|
| **v1 - Compose** (full feature coverage) | `10r5J-DWoFajfp9-rmvL_UqF33jJY_9XQ84lDxffNfIY` | 11 |
| **v1 - Post-edits** (9 surgical edits) | Same doc, mutated | 11 |
| **v2 - Enhanced** (L1 memo patterns) | `18BmXoZDLfq7MM3OETtNE5djgofvizbiTiuK2Bgo0PB0` | 4 |
| **v3 - Regression** (all 3 findings fixed) | `1TYBkFtZU5U7dMCIKUS9Dl2_5yIX8NGlspB-6NGrb2Xg` | 4 |

---

## Compose phase - feature coverage (v1 + v2 combined)

### Document structure (Tier 1)

| Element | Status | Notes |
|---------|--------|-------|
| H1 -> TITLE | PASS | Large title style |
| H2 immediately after H1 -> SUBTITLE | PASS | Lighter subtitle, detected correctly |
| Italic metadata line | PASS | De-emphasized between title and content |
| Numbered H2 sections -> HEADING_1 + green bg | PASS | 5 sections + "Next steps" |

### Heading variations (Tier 2)

| Element | Status | Notes |
|---------|--------|-------|
| H3 phase + auto-emoji (data keyword) | PASS | 📊 prepended |
| H3 phase + auto-emoji (computation) | PASS | ⚙️ prepended |
| H3 phase + auto-emoji (judgment) | PASS | 🎯 prepended |
| H3 phase + auto-emoji (lock/sign-off) | PASS | 🔒 prepended |
| H3 phase + auto-emoji (communication) | PASS | 📤 prepended |
| Parenthetical de-emphasis "(Name)" | PASS | Smaller, grayer font on all variants |
| H4 metadata -> info box (2 sections) | PASS | Where + Effort in green box |
| H4 metadata -> info box (3 sections) | PASS | Where + Effort + Note on sequencing |
| H4 metadata -> info box with bullets | PASS | Participating teams with 3 bullet items inside box (v2) |
| H5 heading -> HEADING_4 | PASS | "Key milestones" |
| Dense section (10+ paragraphs) | PASS | Extra spacing before next heading |

### Text formatting (Tier 3)

| Element | Status | Notes |
|---------|--------|-------|
| Bold terms in body text | PASS | Multiple instances throughout |
| Italic emphasis | PASS | Including full-paragraph italic |
| Bold + italic combined | PASS | "statistical optimization", "operational reality" |
| Inline code (backtick) | PASS | Courier font rendering |
| Hyperlinks | PASS | Blue underlined |
| Footnotes (dagger markers †‡) | PASS | Small gray italic, both markers work |

### Lists (Tier 4)

| Element | Status | Notes |
|---------|--------|-------|
| Bullet list + bold leads | PASS | Legend, phase items |
| Numbered list + bold leads | PASS | Design principles, channel list |
| Nested bullets (2 levels) | PASS | Sub-items with hollow circles |
| Mixed nesting (numbered -> bullet sub-items) | PASS | Fixed: adjacent items merged by nesting level, not type. Numbered parents show 1/2/3, sub-items show a/b. Separate bullet lists stay separate. |

### Tables (Tier 5)

| Element | Status | Notes |
|---------|--------|-------|
| 4-col table (progress overview) | PASS | DOT borders, warm gray header |
| 5-col table (severity, quality gates) | PASS | Content wraps, bold first column |
| 6-col table (lever inventory, RACI) | PASS | Single-letter RACI cells work |
| 7-col table (metrics inventory) | PASS | Widest table, readable at page width |
| 7-col table (accuracy targets) | PASS | Confirms 7-col is the practical max |
| Adjacent tables (back-to-back) | PASS | Proper spacing between them |
| Table borders (DOT/0.5pt/charcoal) | PASS | All 10 tables correct |
| Header row (warm gray bg + bold) | PASS | All 10 tables correct |

### Special elements (Tier 6)

| Element | Status | Notes |
|---------|--------|-------|
| Legend box (warm gray wrapper) | PASS | Fixed: _process_tokens detects LEGEND sections and routes to _process_section_box. H4s as bold, bullets inside box. HEADING_2 style (no green bg). |
| Guidance box (light blue wrapper) | PASS | Fixed: _process_tokens detects GUIDANCE sections and routes to _process_section_box. Paragraph content inside box. HEADING_2 style (no green bg). |
| Blockquote (> prefix) | PASS | Left indentation visible |
| Code block (fenced) | PASS | Courier New, gray bg, indented |
| Step metadata (backtick-prefixed) | PASS | 9pt gray de-emphasis |
| Rich metadata (4-5 pipe-separated fields) | PASS | Wraps correctly across lines (v2) |
| Numbered bold step headings | PASS | "1. Federico downloads..." pattern (v2) |

### Edge cases (Tier 7)

| Element | Status | Notes |
|---------|--------|-------|
| Table immediately after heading | PASS | No gap issues |
| Bold spanning entire bullet | PASS | Works correctly |
| Italic with bold inside | PASS | Renders correctly |
| Long cell content wrapping | PASS | Multi-line cells work |
| Empty-ish sections | PASS | No visual artifacts |

---

## Surgical edit phase - revise flow (9 edits on v1)

| # | Operation | Type | Status | Notes |
|---|-----------|------|--------|-------|
| 1 | Replace "S5 - Informational" -> "S5 - Advisory" | replaceText (table cell) | PASS | Cell content changed, table borders survived |
| 2 | Bold "approximately 12 person-hours per week" | updateTextStyle | PASS | Bold applied to exact range, no bleed |
| 3 | Insert "Risk assessment matrix" table after heading | --insert-table | PASS | DOT borders, warm gray header auto-applied |
| 4 | Insert "Escalation protocols" section with bullets | --insert-section | PASS | HEADING_2 + bullets, no style bleed |
| 5 | Change paragraph to HEADING_3 | updateParagraphStyle | PASS | Paragraph became teal heading |
| 6 | Delete "Stakeholder communication" paragraph | deleteContent | PASS | Content gone, surrounding text flows naturally |
| 7 | Batch: 2 replaceText + 1 updateTextStyle (bold red) | batch (3 ops) | PASS | All three landed in single pass |
| 8 | Change cell background to pink | updateTableCellStyle | PASS | Only targeted cell changed |
| 9 | Convert Next steps bullets to checkboxes | createChecklist | PASS | Checkbox markers visible in PDF |

---

## Summary scorecard

| Category | Tested | Passed | Findings | Pass rate |
|----------|--------|--------|----------|-----------|
| Document structure | 4 | 4 | 0 | 100% |
| Heading variations | 11 | 11 | 0 | 100% |
| Text formatting | 6 | 6 | 0 | 100% |
| Lists | 4 | 4 | 0 | 100% |
| Tables | 8 | 8 | 0 | 100% |
| Special elements | 9 | 9 | 0 | 100% |
| Edge cases | 5 | 5 | 0 | 100% |
| Surgical edits | 9 | 9 | 0 | 100% |
| **Total** | **56** | **56** | **0** | **100%** |

---

## Findings detail (all resolved)

### Finding 1: Legend box not rendered - RESOLVED

**Severity**: Medium
**Element**: H2 "Legend" section with H4 sub-headings
**Root cause**: Pre-scan model correctly classified sections, but `_process_tokens()` never checked for LEGEND/GUIDANCE section types after heading processing. The rendering pipeline only checked for H3 phase metadata.
**Fix**: Added legend/guidance detection in `_process_tokens()` after `_process_heading()` returns. Routes to existing `_process_section_box()` method. Heading styled as HEADING_2 (no green bg) instead of HEADING_1.

### Finding 2: Guidance box not rendered - RESOLVED

**Severity**: Medium
**Element**: H2 "How to use this document" section
**Root cause**: Same as Finding 1 - missing routing in the rendering pipeline.
**Fix**: Same code path as Finding 1. GUIDANCE sections routed to `_process_section_box()` with "guidance" box_type (light blue bg).

### Finding 3: Mixed numbered/bullet nesting - RESOLVED

**Severity**: Low
**Element**: Section 5 - numbered items (1-3) with bullet sub-items
**Root cause**: List grouping in `generate_batch_requests()` only merged adjacent items of the same `ordered` type. Mixed nesting (numbered parent + bullet children) created 3+ separate groups, each getting independent `createParagraphBullets` calls. This broke continuous numbering.
**Fix**: Changed grouping to merge adjacent items when they're nested children (nesting_level > 0) regardless of type, or same-type siblings at root level. Different types at root level correctly start new groups.

---

## What was NOT tested (out of scope)

- Page break behavior inside info boxes (Google Docs rendering, not API-controllable)
- Right-to-left text
- Embedded images
- Table of contents generation
- Multi-tab documents (tested separately)
