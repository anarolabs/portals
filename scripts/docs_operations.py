#!/usr/bin/env python3
"""
Unified Google Docs operations supporting multiple projects.
Uses service account with domain-wide delegation.

Supports:
- anaro-labs: roman@anarolabs.com
- estate-mate: roman@estatemate.io

For full markdown-to-gdocs conversion with proper formatting, see:
~/Documents/Claude Code/portals/portals/adapters/gdocs/converter.py

Usage:
    # Create new document (auto-detect project from file path)
    python3 docs_operations.py --create --title "Document Title" [--content "Initial content"]

    # Create with markdown formatting (explicit project)
    python3 docs_operations.py --create-md --title "Document Title" --file /path/to/content.md --project estate-mate

    # Update existing document with markdown (replaces content)
    python3 docs_operations.py --update-md DOC_ID --file /path/to/content.md

    # Read document content
    python3 docs_operations.py --read DOC_ID --project anaro-labs

    # Get document structure
    python3 docs_operations.py --structure DOC_ID

    # Append text to document
    python3 docs_operations.py --append DOC_ID --text "Text to append"

    # Apply batch updates
    python3 docs_operations.py --batch DOC_ID --requests '[{"insertText": ...}]'

    # List tabs in a document
    python3 docs_operations.py --list-tabs DOC_ID --project estate-mate

    # Create a new tab
    python3 docs_operations.py --create-tab DOC_ID --title "New Tab" --project estate-mate

    # Create a new tab and populate it with formatted markdown
    python3 docs_operations.py --create-tab-md DOC_ID --title "Tab Title" --file /path/to/content.md

    # Delete a tab
    python3 docs_operations.py --delete-tab DOC_ID --tab TAB_ID --project estate-mate

    # Inspect document structure (indices, styles, heading map)
    python3 docs_operations.py --inspect DOC_ID

    # Apply targeted edits from operations file
    python3 docs_operations.py --patch DOC_ID --ops fixes.json

    # Insert formatted table after a heading
    python3 docs_operations.py --insert-table DOC_ID --after-heading "Next steps" --file table.json

    # Insert formatted markdown section after a heading
    python3 docs_operations.py --insert-section DOC_ID --after-heading "Executive summary" --file section.md

    # Export document as PDF
    python3 docs_operations.py --export-pdf DOC_ID [--output /path/to/output.pdf] --project anaro-labs

    # Find text in document (returns character index ranges)
    python3 docs_operations.py --find-text DOC_ID --text "exact phrase to find"

    # Find heading and its section boundaries
    python3 docs_operations.py --find-heading DOC_ID --text "Section 5"

    # Check comment anchors (read-only, shows positions and quoted content)
    python3 docs_operations.py --check-comments DOC_ID --project anaro-labs
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from google_client import get_docs_service, get_drive_service, _resolve_project, PROJECT_CONFIG


# =============================================================================
# Element extraction helpers (for comprehensive document parsing)
# =============================================================================

def extract_paragraph_element(para_element, inline_objects=None):
    """Extract text/placeholder from any paragraph element type.

    Handles all 10 ParagraphElement types from Google Docs API:
    textRun, inlineObjectElement, richLink, person, horizontalRule,
    pageBreak, autoText, equation, footnoteReference, columnBreak
    """
    if "textRun" in para_element:
        return para_element["textRun"].get("content", "")

    elif "inlineObjectElement" in para_element:
        obj_id = para_element["inlineObjectElement"].get("inlineObjectId")
        if inline_objects and obj_id in inline_objects:
            obj = inline_objects[obj_id].get("inlineObjectProperties", {})
            embedded = obj.get("embeddedObject", {})
            title = embedded.get("title", "")
            desc = embedded.get("description", "")
            return f"[Image: {title or desc or 'untitled'}]"
        return "[Image]"

    elif "richLink" in para_element:
        props = para_element["richLink"].get("richLinkProperties", {})
        title = props.get("title", "")
        uri = props.get("uri", "")
        return f"[Link: {title or uri}]"

    elif "person" in para_element:
        props = para_element["person"].get("personProperties", {})
        name = props.get("name", props.get("email", ""))
        return f"@{name}" if name else ""

    elif "horizontalRule" in para_element:
        return "\n---\n"

    elif "pageBreak" in para_element:
        return "\n[Page Break]\n"

    elif "autoText" in para_element:
        # Page numbers, etc. - just show placeholder
        auto_type = para_element["autoText"].get("type", "")
        return f"[{auto_type}]" if auto_type else ""

    # equation, footnoteReference, columnBreak - minimal handling
    elif "footnoteReference" in para_element:
        footnote_id = para_element["footnoteReference"].get("footnoteId", "")
        return f"[^{footnote_id}]" if footnote_id else ""

    return ""


def extract_table(table_element, inline_objects=None):
    """Extract table as markdown-style text with | delimiters."""
    rows = []
    for row in table_element.get("tableRows", []):
        cells = []
        for cell in row.get("tableCells", []):
            cell_text = []
            for content in cell.get("content", []):
                cell_text.append(extract_structural_element(content, inline_objects))
            cells.append("".join(cell_text).strip().replace("\n", " "))
        rows.append(" | ".join(cells))
    return "\n".join(rows) + "\n"


def extract_structural_element(element, inline_objects=None):
    """Extract text from any structural element (paragraph, table, etc.)."""
    if "paragraph" in element:
        parts = []
        for para_elem in element["paragraph"].get("elements", []):
            parts.append(extract_paragraph_element(para_elem, inline_objects))
        return "".join(parts)

    elif "table" in element:
        return extract_table(element["table"], inline_objects)

    elif "tableOfContents" in element:
        return "[Table of Contents]\n"

    # sectionBreak has no text content
    return ""


# =============================================================================
# Surgical editing infrastructure
# =============================================================================

# Standard table formatting constants (both workspaces, always)
TABLE_BORDER_COLOR = (0.165, 0.165, 0.227)   # dark charcoal #2a2a3a
TABLE_HEADER_BG = (0.961, 0.957, 0.941)       # warm gray #f5f4f0
TABLE_BORDER_WIDTH = 0.5
TABLE_BORDER_STYLE = "DOT"


def _make_border_def(color=TABLE_BORDER_COLOR, width=TABLE_BORDER_WIDTH, style=TABLE_BORDER_STYLE):
    """Build a Google Docs API border definition."""
    return {
        "color": {"color": {"rgbColor": {"red": color[0], "green": color[1], "blue": color[2]}}},
        "width": {"magnitude": width, "unit": "PT"},
        "dashStyle": style,
    }


def _make_rgb(color):
    """Build an RGB color dict from a tuple."""
    return {"rgbColor": {"red": color[0], "green": color[1], "blue": color[2]}}


def _build_heading_map(body, inline_objects=None):
    """Build heading map with section boundaries from document body content.

    Returns dict mapping heading text -> {startIndex, endIndex, style, sectionEnd}.
    sectionEnd is the startIndex of the next heading at same or higher level,
    or the document end if no such heading exists.
    """
    headings = []
    for element in body:
        if "paragraph" not in element:
            continue
        para = element["paragraph"]
        style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        if not style.startswith("HEADING_"):
            continue

        level = int(style.split("_")[1])
        text_parts = []
        for para_elem in para.get("elements", []):
            text_parts.append(extract_paragraph_element(para_elem, inline_objects))
        text = "".join(text_parts).strip()

        headings.append({
            "text": text,
            "style": style,
            "startIndex": element.get("startIndex", 0),
            "endIndex": element.get("endIndex", 0),
            "level": level,
        })

    heading_map = {}
    for i, h in enumerate(headings):
        section_end = None
        for j in range(i + 1, len(headings)):
            if headings[j]["level"] <= h["level"]:
                section_end = headings[j]["startIndex"]
                break
        if section_end is None and body:
            section_end = body[-1].get("endIndex", 0)

        key = h["text"]
        if key in heading_map:
            counter = 2
            while f"{key} ({counter})" in heading_map:
                counter += 1
            key = f"{key} ({counter})"

        entry = {
            "startIndex": h["startIndex"],
            "endIndex": h["endIndex"],
            "style": h["style"],
        }
        if section_end is not None:
            entry["sectionEnd"] = section_end
        heading_map[key] = entry

    return heading_map


def _find_table_cells(body, near_index):
    """Find cell paragraph start positions in a table near the given index.

    Returns (cell_positions, table_start, table_end) where cell_positions[row][col]
    is the paragraph startIndex of that cell.
    """
    for element in body:
        if "table" not in element:
            continue
        if abs(element.get("startIndex", 0) - near_index) > 10:
            continue

        table = element["table"]
        cell_positions = []
        for row in table.get("tableRows", []):
            row_positions = []
            for cell in row.get("tableCells", []):
                content = cell.get("content", [])
                if content:
                    row_positions.append(content[0].get("startIndex", 0))
                else:
                    row_positions.append(None)
            cell_positions.append(row_positions)

        return cell_positions, element.get("startIndex"), element.get("endIndex")

    return None, None, None


def _offset_indices(obj, offset):
    """Recursively add offset to all index fields in API request objects."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in ("index", "startIndex", "endIndex"):
                if isinstance(obj[key], (int, float)):
                    obj[key] += offset
            else:
                _offset_indices(obj[key], offset)
    elif isinstance(obj, list):
        for item in obj:
            _offset_indices(item, offset)


def create_document(title: str, content: str = None, folder_id: str = None, project: str = None, file_path: str = None):
    """Create a new Google Doc."""
    resolved_project = _resolve_project(project, file_path)
    docs_service = get_docs_service(project=resolved_project)
    drive_service = get_drive_service(project=resolved_project)

    try:
        # Create the document
        doc = docs_service.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]

        # If content provided, insert it
        if content:
            requests = [{
                "insertText": {
                    "location": {"index": 1},
                    "text": content
                }
            }]
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": requests}
            ).execute()

        # If folder specified, move the file
        if folder_id:
            # Get current parents
            file = drive_service.files().get(fileId=doc_id, fields="parents").execute()
            previous_parents = ",".join(file.get("parents", []))

            # Move to new folder
            drive_service.files().update(
                fileId=doc_id,
                addParents=folder_id,
                removeParents=previous_parents,
                fields="id, parents"
            ).execute()

        # Get the document URL
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "title": title,
            "url": doc_url,
            "project": resolved_project,
            "account": PROJECT_CONFIG[resolved_project]["impersonate_user"]
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def create_from_markdown(title: str, markdown_file: str, folder_id: str = None, project: str = None):
    """
    Create a document from markdown with proper formatting.
    Uses the portals converter if available, otherwise falls back to plain text.
    """
    resolved_project = _resolve_project(project, markdown_file)
    docs_service = get_docs_service(project=resolved_project)
    drive_service = get_drive_service(project=resolved_project)

    # Read markdown file
    md_path = Path(markdown_file)
    if not md_path.exists():
        print(json.dumps({"error": f"File not found: {markdown_file}"}), file=sys.stderr)
        sys.exit(1)

    markdown_content = md_path.read_text()

    try:
        # Try to use the portals converter for proper formatting
        portals_converter_path = Path.home() / "Documents/Claude Code/portals/portals/adapters/gdocs/converter.py"

        if portals_converter_path.exists():
            # Add portals to path and import converter
            sys.path.insert(0, str(portals_converter_path.parent.parent.parent.parent))
            from portals.adapters.gdocs.converter import GoogleDocsConverter

            converter = GoogleDocsConverter()
            conversion = converter.markdown_to_gdocs(markdown_content)
            plain_text = conversion.plain_text
            batch_requests = converter.generate_batch_requests(conversion)

            # Create document with plain text
            doc = docs_service.documents().create(body={"title": title}).execute()
            doc_id = doc["documentId"]

            # Insert plain text first
            if plain_text:
                docs_service.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": plain_text}}]}
                ).execute()

            # Apply formatting
            if batch_requests:
                docs_service.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": batch_requests}
                ).execute()

            formatted = True

        else:
            # Fallback: just insert markdown as plain text
            doc = docs_service.documents().create(body={"title": title}).execute()
            doc_id = doc["documentId"]

            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": markdown_content}}]}
            ).execute()

            formatted = False

        # Move to folder if specified
        if folder_id:
            file = drive_service.files().get(fileId=doc_id, fields="parents").execute()
            previous_parents = ",".join(file.get("parents", []))
            drive_service.files().update(
                fileId=doc_id,
                addParents=folder_id,
                removeParents=previous_parents,
                fields="id, parents"
            ).execute()

        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "title": title,
            "url": doc_url,
            "formatted": formatted,
            "project": resolved_project,
            "account": PROJECT_CONFIG[resolved_project]["impersonate_user"],
            "note": "Using portals converter" if formatted else "Plain text (converter not found)"
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def read_document(doc_id: str, project: str = None, tab_id: str = None):
    """Read document content including tables, images, and other elements."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        if tab_id:
            doc = docs_service.documents().get(
                documentId=doc_id, includeTabsContent=True
            ).execute()
            # Find the target tab's body and inline objects
            inline_objects = {}
            body = []
            for tab in doc.get("tabs", []):
                if tab.get("tabProperties", {}).get("tabId") == tab_id:
                    doc_tab = tab.get("documentTab", {})
                    inline_objects = doc_tab.get("inlineObjects", {})
                    body = doc_tab.get("body", {}).get("content", [])
                    break
            if not body:
                print(json.dumps({"error": f"Tab {tab_id} not found"}), file=sys.stderr)
                sys.exit(1)
        else:
            doc = docs_service.documents().get(documentId=doc_id).execute()
            inline_objects = doc.get("inlineObjects", {})
            body = doc.get("body", {}).get("content", [])

        # Extract text from all structural elements
        content = []
        for element in body:
            content.append(extract_structural_element(element, inline_objects))

        print(json.dumps({
            "document_id": doc_id,
            "title": doc.get("title"),
            "content": "".join(content),
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "project": resolved_project
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def get_structure(doc_id: str, project: str = None):
    """Get document structure (for debugging/inspection)."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()

        # Get inline objects for reference
        inline_objects = doc.get("inlineObjects", {})
        body = doc.get("body", {}).get("content", [])
        elements = []

        for i, element in enumerate(body):
            if "paragraph" in element:
                para = element["paragraph"]
                style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
                text_preview = ""
                has_image = False
                has_link = False
                for para_elem in para.get("elements", []):
                    if "textRun" in para_elem:
                        text_preview += para_elem["textRun"].get("content", "")[:50]
                    elif "inlineObjectElement" in para_elem:
                        has_image = True
                    elif "richLink" in para_elem:
                        has_link = True
                elem_info = {
                    "index": i,
                    "type": "paragraph",
                    "style": style,
                    "preview": text_preview.strip()[:50]
                }
                if has_image:
                    elem_info["has_image"] = True
                if has_link:
                    elem_info["has_link"] = True
                elements.append(elem_info)

            elif "table" in element:
                table = element["table"]
                rows = table.get("tableRows", [])
                row_count = len(rows)
                col_count = len(rows[0].get("tableCells", [])) if rows else 0
                # Get first row as preview
                first_row_preview = []
                if rows:
                    for cell in rows[0].get("tableCells", [])[:3]:  # Max 3 cells
                        cell_text = ""
                        for content in cell.get("content", []):
                            cell_text += extract_structural_element(content, inline_objects)
                        first_row_preview.append(cell_text.strip()[:20])
                elements.append({
                    "index": i,
                    "type": "table",
                    "rows": row_count,
                    "cols": col_count,
                    "first_row_preview": " | ".join(first_row_preview)
                })

            elif "sectionBreak" in element:
                elements.append({"index": i, "type": "sectionBreak"})

            elif "tableOfContents" in element:
                elements.append({"index": i, "type": "tableOfContents"})

        print(json.dumps({
            "document_id": doc_id,
            "title": doc.get("title"),
            "element_count": len(elements),
            "inline_object_count": len(inline_objects),
            "elements": elements,
            "project": resolved_project
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def append_text(doc_id: str, text: str, project: str = None):
    """Append text to end of document."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        # Get current document to find end index
        doc = docs_service.documents().get(documentId=doc_id).execute()
        body = doc.get("body", {}).get("content", [])

        # Find end index (last element's endIndex - 1)
        end_index = 1
        if body:
            last_element = body[-1]
            end_index = last_element.get("endIndex", 1) - 1

        # Insert text at end
        requests = [{
            "insertText": {
                "location": {"index": end_index},
                "text": "\n" + text if end_index > 1 else text
            }
        }]

        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests}
        ).execute()

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "appended_length": len(text),
            "project": resolved_project
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def batch_update(doc_id: str, requests_json: str, project: str = None):
    """Apply batch update requests to document."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        requests = json.loads(requests_json)

        result = docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests}
        ).execute()

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "replies": result.get("replies", []),
            "project": resolved_project
        }, indent=2))

    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {str(e)}"}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


# =============================================================================
# Surgical editing operations
# =============================================================================


def inspect_document(doc_id: str, project: str = None):
    """Inspect document structure with indices, styles, and heading map."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        inline_objects = doc.get("inlineObjects", {})
        body = doc.get("body", {}).get("content", [])

        elements = []
        for element in body:
            start_idx = element.get("startIndex", 0)
            end_idx = element.get("endIndex", 0)

            if "paragraph" in element:
                para = element["paragraph"]
                style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
                text_parts = []
                for para_elem in para.get("elements", []):
                    text_parts.append(extract_paragraph_element(para_elem, inline_objects))
                full_text = "".join(text_parts).strip()

                elem_info = {
                    "type": "paragraph",
                    "style": style,
                    "text": full_text[:80],
                    "startIndex": start_idx,
                    "endIndex": end_idx,
                }
                bullet = para.get("bullet")
                if bullet:
                    elem_info["listId"] = bullet.get("listId", "")
                    elem_info["nestingLevel"] = bullet.get("nestingLevel", 0)
                elements.append(elem_info)

            elif "table" in element:
                table = element["table"]
                rows = table.get("tableRows", [])
                row_count = len(rows)
                col_count = len(rows[0].get("tableCells", [])) if rows else 0

                header_row = []
                if rows:
                    for cell in rows[0].get("tableCells", []):
                        cell_text = []
                        for content in cell.get("content", []):
                            cell_text.append(extract_structural_element(content, inline_objects))
                        header_row.append("".join(cell_text).strip()[:40])

                borders = {}
                header_bg = None
                if rows and rows[0].get("tableCells"):
                    first_style = rows[0]["tableCells"][0].get("tableCellStyle", {})
                    bt = first_style.get("borderTop", {})
                    if bt:
                        borders["style"] = bt.get("dashStyle", "SOLID")
                        borders["width"] = bt.get("width", {}).get("magnitude", 1)
                        c = bt.get("color", {}).get("color", {}).get("rgbColor", {})
                        borders["color"] = [
                            round(c.get("red", 0), 3),
                            round(c.get("green", 0), 3),
                            round(c.get("blue", 0), 3),
                        ]
                    bg = first_style.get("backgroundColor", {}).get("color", {}).get("rgbColor", {})
                    if bg:
                        header_bg = [
                            round(bg.get("red", 0), 3),
                            round(bg.get("green", 0), 3),
                            round(bg.get("blue", 0), 3),
                        ]

                elem_info = {
                    "type": "table",
                    "rows": row_count,
                    "cols": col_count,
                    "startIndex": start_idx,
                    "endIndex": end_idx,
                    "headerRow": header_row,
                }
                if borders:
                    elem_info["borders"] = borders
                if header_bg:
                    elem_info["headerBg"] = header_bg
                elements.append(elem_info)

            elif "sectionBreak" in element:
                elements.append({"type": "sectionBreak", "startIndex": start_idx, "endIndex": end_idx})

            elif "tableOfContents" in element:
                elements.append({"type": "tableOfContents", "startIndex": start_idx, "endIndex": end_idx})

        heading_map = _build_heading_map(body, inline_objects)

        print(json.dumps({
            "document_id": doc_id,
            "title": doc.get("title"),
            "elements": elements,
            "headingMap": heading_map,
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def find_text(doc_id: str, search_text: str, project: str = None):
    """Find exact text in document and return character index ranges.

    Searches the full document text for all occurrences of the search string.
    Returns startIndex and endIndex for each match, plus surrounding context.
    """
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        inline_objects = doc.get("inlineObjects", {})
        body = doc.get("body", {}).get("content", [])

        # Build a mapping of document text position -> character index
        # We need to track the actual document indices, not just string positions
        text_segments = []  # (text, startIndex, endIndex)
        for element in body:
            if "paragraph" in element:
                para = element["paragraph"]
                for para_elem in para.get("elements", []):
                    if "textRun" in para_elem:
                        content = para_elem["textRun"].get("content", "")
                        start = para_elem.get("startIndex", 0)
                        end = para_elem.get("endIndex", 0)
                        text_segments.append((content, start, end))

        # Concatenate all text with position tracking
        full_text = ""
        char_to_index = []  # maps position in full_text -> document index
        for content, start, end in text_segments:
            for i, char in enumerate(content):
                char_to_index.append(start + i)
            full_text += content

        # Find all occurrences
        matches = []
        search_lower = search_text.lower()
        text_lower = full_text.lower()
        pos = 0
        while True:
            pos = text_lower.find(search_lower, pos)
            if pos == -1:
                break
            match_start = char_to_index[pos]
            match_end = char_to_index[pos + len(search_text) - 1] + 1

            # Extract context (50 chars before and after)
            ctx_start = max(0, pos - 50)
            ctx_end = min(len(full_text), pos + len(search_text) + 50)
            context = full_text[ctx_start:ctx_end].replace("\n", "\\n")

            matches.append({
                "startIndex": match_start,
                "endIndex": match_end,
                "matchedText": full_text[pos:pos + len(search_text)],
                "context": context,
            })
            pos += 1

        print(json.dumps({
            "document_id": doc_id,
            "search": search_text,
            "matchCount": len(matches),
            "matches": matches,
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def find_heading(doc_id: str, search_text: str, project: str = None):
    """Find a heading by text and return its section boundaries.

    Returns startIndex, endIndex of the heading element itself,
    plus sectionEnd (start of next heading at same or higher level).
    Uses case-insensitive substring matching.
    """
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        inline_objects = doc.get("inlineObjects", {})
        body = doc.get("body", {}).get("content", [])

        heading_map = _build_heading_map(body, inline_objects)

        # Find matching headings (case-insensitive substring)
        search_lower = search_text.lower()
        matches = []
        for heading_text, info in heading_map.items():
            if search_lower in heading_text.lower():
                matches.append({
                    "headingText": heading_text,
                    "style": info["style"],
                    "startIndex": info["startIndex"],
                    "endIndex": info["endIndex"],
                    "sectionEnd": info.get("sectionEnd"),
                })

        print(json.dumps({
            "document_id": doc_id,
            "search": search_text,
            "matchCount": len(matches),
            "matches": matches,
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def check_comments(doc_id: str, project: str = None):
    """Check comment anchors on a document (read-only).

    Uses Drive API to fetch all comments with their anchor positions
    and quotedFileContent. Useful for verifying comments survive edits.
    """
    resolved_project = _resolve_project(project)
    drive_service = get_drive_service(project=resolved_project)

    try:
        comments = []
        page_token = None
        while True:
            resp = drive_service.comments().list(
                fileId=doc_id,
                fields="comments(id,content,author/displayName,quotedFileContent,anchor,resolved,createdTime,modifiedTime,replies(content,author/displayName,createdTime)),nextPageToken",
                includeDeleted=False,
                pageToken=page_token,
            ).execute()
            comments.extend(resp.get("comments", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        results = []
        for c in comments:
            entry = {
                "id": c.get("id"),
                "author": c.get("author", {}).get("displayName", ""),
                "content": c.get("content", ""),
                "resolved": c.get("resolved", False),
                "createdTime": c.get("createdTime", ""),
            }
            quoted = c.get("quotedFileContent")
            if quoted:
                entry["quotedText"] = quoted.get("value", "")
                entry["quotedMimeType"] = quoted.get("mimeType", "")
            anchor = c.get("anchor")
            if anchor:
                entry["anchor"] = anchor
            replies = c.get("replies", [])
            if replies:
                entry["replies"] = [
                    {
                        "author": r.get("author", {}).get("displayName", ""),
                        "content": r.get("content", ""),
                        "createdTime": r.get("createdTime", ""),
                    }
                    for r in replies
                ]
            results.append(entry)

        active = [c for c in results if not c.get("resolved")]
        resolved = [c for c in results if c.get("resolved")]

        print(json.dumps({
            "document_id": doc_id,
            "totalComments": len(results),
            "activeComments": len(active),
            "resolvedComments": len(resolved),
            "comments": results,
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def patch_document(doc_id: str, ops_file: str, project: str = None):
    """Apply targeted element-level edits from an operations JSON file.

    Supports: replaceText, replaceMarkdown, insertText, deleteContent,
    updateParagraphStyle, updateTextStyle, updateTableCellStyle,
    updateTableBorders, createChecklist.
    Content-modifying operations are auto-sorted in reverse index order.

    replaceMarkdown deletes [startIndex, endIndex) then inserts formatted markdown
    at startIndex using the converter (headings, bold, italic, native bullet lists,
    etc.). Formatting is applied in a second batchUpdate pass.
    """
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    ops_path = Path(ops_file)
    if not ops_path.exists():
        print(json.dumps({"error": f"File not found: {ops_file}"}), file=sys.stderr)
        sys.exit(1)

    ops = json.loads(ops_path.read_text())
    requests = []
    content_ops = []  # (sort_key, [requests])
    style_ops = []
    markdown_format_ops = []  # (startIndex, plain_text, batch_requests) for second pass

    for op in ops:
        op_type = op.get("type")

        if op_type == "replaceText":
            content_ops.append((op["startIndex"], [
                {"deleteContentRange": {"range": {"startIndex": op["startIndex"], "endIndex": op["endIndex"]}}},
                {"insertText": {"location": {"index": op["startIndex"]}, "text": op["newText"]}},
            ]))
        elif op_type == "replaceMarkdown":
            # Load converter on first use
            portals_path = Path.home() / "Documents/Claude Code/portals/portals/adapters/gdocs/converter.py"
            if not portals_path.exists():
                print(json.dumps({"error": "Converter not found at portals/portals/adapters/gdocs/converter.py"}), file=sys.stderr)
                sys.exit(1)
            if str(portals_path.parent.parent.parent.parent) not in sys.path:
                sys.path.insert(0, str(portals_path.parent.parent.parent.parent))
            from portals.adapters.gdocs.converter import GoogleDocsConverter

            converter = GoogleDocsConverter()
            conversion = converter.markdown_to_gdocs(op["markdown"])
            plain_text = conversion.plain_text
            batch_requests = converter.generate_batch_requests(conversion)

            # Content part: delete range then insert plain text (sorted with other content ops)
            content_ops.append((op["startIndex"], [
                {"deleteContentRange": {"range": {"startIndex": op["startIndex"], "endIndex": op["endIndex"]}}},
                {"insertText": {"location": {"index": op["startIndex"]}, "text": plain_text}},
            ]))

            # Formatting part: applied in a second batchUpdate after text is in place
            if batch_requests:
                markdown_format_ops.append((op["startIndex"], plain_text, batch_requests))
        elif op_type == "insertText":
            content_ops.append((op["index"], [
                {"insertText": {"location": {"index": op["index"]}, "text": op["text"]}},
            ]))
        elif op_type == "deleteContent":
            content_ops.append((op["startIndex"], [
                {"deleteContentRange": {"range": {"startIndex": op["startIndex"], "endIndex": op["endIndex"]}}},
            ]))
        elif op_type == "updateParagraphStyle":
            style_ops.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": op["startIndex"], "endIndex": op["endIndex"]},
                    "paragraphStyle": op["paragraphStyle"],
                    "fields": op.get("fields", "namedStyleType"),
                }
            })
        elif op_type == "updateTextStyle":
            style_ops.append({
                "updateTextStyle": {
                    "range": {"startIndex": op["startIndex"], "endIndex": op["endIndex"]},
                    "textStyle": op["textStyle"],
                    "fields": op.get("fields", "bold"),
                }
            })
        elif op_type == "updateTableCellStyle":
            cell_style = {}
            fields_list = []
            s = op.get("style", {})
            if "backgroundColor" in s:
                cell_style["backgroundColor"] = {"color": _make_rgb(s["backgroundColor"])}
                fields_list.append("backgroundColor")
            style_ops.append({
                "updateTableCellStyle": {
                    "tableRange": {
                        "tableCellLocation": {
                            "tableStartLocation": {"index": op["tableStartIndex"]},
                            "rowIndex": op.get("row", 0),
                            "columnIndex": op.get("col", 0),
                        },
                        "rowSpan": op.get("rowSpan", 1),
                        "columnSpan": op.get("colSpan", 1),
                    },
                    "tableCellStyle": cell_style,
                    "fields": op.get("fields", ",".join(fields_list)),
                }
            })
        elif op_type == "updateTableBorders":
            border = _make_border_def(
                color=tuple(op.get("color", list(TABLE_BORDER_COLOR))),
                width=op.get("width", TABLE_BORDER_WIDTH),
                style=op.get("borderStyle", TABLE_BORDER_STYLE),
            )
            style_ops.append({
                "updateTableCellStyle": {
                    "tableRange": {
                        "tableCellLocation": {
                            "tableStartLocation": {"index": op["tableStartIndex"]},
                            "rowIndex": 0,
                            "columnIndex": 0,
                        },
                        "rowSpan": op.get("rows", 1),
                        "columnSpan": op.get("cols", 1),
                    },
                    "tableCellStyle": {
                        "borderTop": border,
                        "borderBottom": border,
                        "borderLeft": border,
                        "borderRight": border,
                    },
                    "fields": "borderTop,borderBottom,borderLeft,borderRight",
                }
            })
        elif op_type == "createChecklist":
            style_ops.append({
                "createParagraphBullets": {
                    "range": {"startIndex": op["startIndex"], "endIndex": op["endIndex"]},
                    "bulletPreset": "BULLET_CHECKBOX",
                }
            })

    # Sort content ops in reverse index order, then flatten
    content_ops.sort(key=lambda x: x[0], reverse=True)
    for _, reqs in content_ops:
        requests.extend(reqs)
    requests.extend(style_ops)

    if not requests and not markdown_format_ops:
        print(json.dumps({"error": "No valid operations found"}), file=sys.stderr)
        sys.exit(1)

    try:
        total_requests_sent = 0

        # Pass 1: content modifications + style ops
        if requests:
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": requests},
            ).execute()
            total_requests_sent += len(requests)

        # Pass 2: markdown formatting (must happen after text is inserted)
        # Converter requests assume text starts at index 1; offset to actual startIndex.
        # Also reset inserted range to NORMAL_TEXT first to clear inherited styles.
        if markdown_format_ops:
            format_requests = []
            for start_index, plain_text, batch_reqs in markdown_format_ops:
                content_end = start_index + len(plain_text)

                # Reset to NORMAL_TEXT to clear inherited heading/spacing styles
                format_requests.append({
                    "updateParagraphStyle": {
                        "range": {"startIndex": start_index, "endIndex": content_end},
                        "paragraphStyle": {
                            "namedStyleType": "NORMAL_TEXT",
                            "spaceAbove": {"magnitude": 0, "unit": "PT"},
                            "spaceBelow": {"magnitude": 0, "unit": "PT"},
                        },
                        "fields": "namedStyleType,spaceAbove,spaceBelow",
                    }
                })

                # Offset converter requests: converter assumes index 1, actual is start_index
                offset = start_index - 1
                offset_reqs = json.loads(json.dumps(batch_reqs))  # deep copy
                _offset_indices(offset_reqs, offset)
                format_requests.extend(offset_reqs)

            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": format_requests},
            ).execute()
            total_requests_sent += len(format_requests)

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "operations_applied": len(ops),
            "batch_requests_sent": total_requests_sent,
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def insert_table(doc_id: str, after_heading: str, table_file: str, heading_text: str = None, project: str = None):
    """Insert a formatted table after a heading with standardized styling.

    Table JSON: {"headers": [...], "rows": [[...], ...], "checklist_column": N}
    Applies standard formatting: dotted borders, warm gray header bg, bold headers.
    """
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    table_path = Path(table_file)
    if not table_path.exists():
        print(json.dumps({"error": f"File not found: {table_file}"}), file=sys.stderr)
        sys.exit(1)

    table_def = json.loads(table_path.read_text())
    headers = table_def.get("headers", [])
    data_rows = table_def.get("rows", [])
    checklist_col = table_def.get("checklist_column")
    num_cols = len(headers)
    num_rows = 1 + len(data_rows)
    all_rows = [headers] + data_rows

    try:
        # Step 1: Fetch doc and find insertion point
        doc = docs_service.documents().get(documentId=doc_id).execute()
        body = doc.get("body", {}).get("content", [])
        inline_objects = doc.get("inlineObjects", {})
        heading_map = _build_heading_map(body, inline_objects)

        if after_heading not in heading_map:
            print(json.dumps({
                "error": f"Heading not found: {after_heading}",
                "available_headings": list(heading_map.keys()),
            }), file=sys.stderr)
            sys.exit(1)

        doc_end = body[-1].get("endIndex", 1) if body else 1
        insert_at = heading_map[after_heading].get("sectionEnd")
        if insert_at is None:
            insert_at = doc_end - 1
        else:
            # Can't insert at document endIndex itself
            insert_at = min(insert_at, doc_end - 1)

        # Step 2: Build batch 1 - insert separator/heading + table structure
        # Key: do NOT add trailing \n after heading text. insertTable auto-inserts
        # a \n before the table which terminates the heading paragraph. Adding our
        # own trailing \n would create an empty paragraph between heading and table.
        # Order: insertText -> insertTable -> updateParagraphStyle (heading paragraph
        # must be terminated by insertTable's auto-\n before we can style it safely).
        batch1 = []
        if heading_text:
            pre_text = "\n" + heading_text
            h_start = insert_at + 1
            h_end = h_start + len(heading_text) + 1  # +1 for insertTable auto-\n
            batch1.append({"insertText": {"location": {"index": insert_at}, "text": pre_text}})
            table_location = insert_at + len(pre_text)
        else:
            table_location = insert_at

        batch1.append({
            "insertTable": {
                "rows": num_rows,
                "columns": num_cols,
                "location": {"index": table_location},
            }
        })

        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": batch1}
        ).execute()

        # Step 3: Refetch to get cell positions
        doc = docs_service.documents().get(documentId=doc_id).execute()
        body = doc.get("body", {}).get("content", [])

        cell_positions, table_start, table_end = _find_table_cells(body, table_location)
        if cell_positions is None:
            print(json.dumps({"error": "Could not find inserted table"}), file=sys.stderr)
            sys.exit(1)

        # Step 3b: Style heading + reset separator using actual indices from refetch.
        # Can't do this in batch1 because insertTable's auto-\n shifts paragraph
        # boundaries unpredictably. After refetch we have ground truth.
        _heading_style_reqs = []
        if heading_text:
            style_reqs = []
            # Find the heading paragraph and separator in the refetched body
            for i, elem in enumerate(body):
                if "table" in elem and abs(elem.get("startIndex", 0) - table_start) <= 10:
                    # Found our table - heading should be the paragraph just before it
                    if i > 0 and "paragraph" in body[i - 1]:
                        hp = body[i - 1]
                        style_reqs.append({
                            "updateParagraphStyle": {
                                "range": {"startIndex": hp["startIndex"], "endIndex": hp["endIndex"]},
                                "paragraphStyle": {"namedStyleType": "HEADING_3"},
                                "fields": "namedStyleType",
                            }
                        })
                    # Separator is 2 elements before the table (before the heading)
                    if i > 1 and "paragraph" in body[i - 2]:
                        sp = body[i - 2]
                        sp_text = "".join(
                            e.get("textRun", {}).get("content", "")
                            for e in sp.get("paragraph", {}).get("elements", [])
                        ).strip()
                        if sp_text == "":  # empty separator paragraph
                            style_reqs.append({
                                "updateParagraphStyle": {
                                    "range": {"startIndex": sp["startIndex"], "endIndex": sp["endIndex"]},
                                    "paragraphStyle": {
                                        "namedStyleType": "NORMAL_TEXT",
                                        "spaceAbove": {"magnitude": 0, "unit": "PT"},
                                        "spaceBelow": {"magnitude": 0, "unit": "PT"},
                                    },
                                    "fields": "namedStyleType,spaceAbove,spaceBelow",
                                }
                            })
                    break
            # Save style_reqs for after batch2 (batch2's cell formatting can
            # interfere with heading styles if applied before)
            _heading_style_reqs = style_reqs

        # Step 4: Build batch 2 - cell text (reverse) + all formatting
        batch2 = []

        # Collect cell data in document order
        cells_in_order = []
        for r in range(num_rows):
            for c in range(num_cols):
                text = str(all_rows[r][c]) if c < len(all_rows[r]) else ""
                pos = cell_positions[r][c] if r < len(cell_positions) and c < len(cell_positions[r]) else None
                cells_in_order.append((r, c, text, pos))

        # Insert text in reverse order (last cell first to avoid index shifting)
        for r, c, text, pos in reversed(cells_in_order):
            if text and pos is not None:
                batch2.append({"insertText": {"location": {"index": pos}, "text": text}})

        # Calculate filled positions after all insertions
        # Each cell shifts by cumulative text length of all preceding cells
        cumulative = 0
        filled = {}
        for r, c, text, pos in cells_in_order:
            filled[(r, c)] = {"start": (pos + cumulative) if pos else None, "len": len(text)}
            cumulative += len(text)
        total_text = cumulative

        # Borders on all cells
        border = _make_border_def()
        batch2.append({
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": table_start},
                        "rowIndex": 0,
                        "columnIndex": 0,
                    },
                    "rowSpan": num_rows,
                    "columnSpan": num_cols,
                },
                "tableCellStyle": {
                    "borderTop": border, "borderBottom": border,
                    "borderLeft": border, "borderRight": border,
                    "paddingTop": {"magnitude": 4, "unit": "PT"},
                    "paddingBottom": {"magnitude": 4, "unit": "PT"},
                    "paddingLeft": {"magnitude": 6, "unit": "PT"},
                    "paddingRight": {"magnitude": 6, "unit": "PT"},
                },
                "fields": "borderTop,borderBottom,borderLeft,borderRight,paddingTop,paddingBottom,paddingLeft,paddingRight",
            }
        })

        # Header row background
        batch2.append({
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": table_start},
                        "rowIndex": 0,
                        "columnIndex": 0,
                    },
                    "rowSpan": 1,
                    "columnSpan": num_cols,
                },
                "tableCellStyle": {
                    "backgroundColor": {"color": _make_rgb(TABLE_HEADER_BG)},
                },
                "fields": "backgroundColor",
            }
        })

        # Bold + dark charcoal text for header row
        for c in range(num_cols):
            fp = filled.get((0, c))
            if fp and fp["start"] is not None and fp["len"] > 0:
                batch2.append({
                    "updateTextStyle": {
                        "range": {"startIndex": fp["start"], "endIndex": fp["start"] + fp["len"]},
                        "textStyle": {
                            "bold": True,
                            "foregroundColor": {"color": _make_rgb(TABLE_BORDER_COLOR)},
                        },
                        "fields": "bold,foregroundColor",
                    }
                })

        # Paragraph style for all cells: NORMAL_TEXT, tight spacing
        adjusted_end = table_end + total_text
        batch2.append({
            "updateParagraphStyle": {
                "range": {"startIndex": table_start + 1, "endIndex": adjusted_end - 1},
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    "lineSpacing": 100,
                    "spaceAbove": {"magnitude": 0, "unit": "PT"},
                    "spaceBelow": {"magnitude": 0, "unit": "PT"},
                },
                "fields": "namedStyleType,lineSpacing,spaceAbove,spaceBelow",
            }
        })

        # Clear font size (use document default)
        batch2.append({
            "updateTextStyle": {
                "range": {"startIndex": table_start + 1, "endIndex": adjusted_end - 1},
                "textStyle": {},
                "fields": "fontSize",
            }
        })

        # Checklist column
        if checklist_col is not None:
            for r in range(1, num_rows):  # skip header row
                fp = filled.get((r, checklist_col))
                if fp and fp["start"] is not None:
                    p_end = fp["start"] + max(fp["len"], 1)
                    batch2.append({
                        "createParagraphBullets": {
                            "range": {"startIndex": fp["start"], "endIndex": p_end},
                            "bulletPreset": "BULLET_CHECKBOX",
                        }
                    })

        if batch2:
            docs_service.documents().batchUpdate(
                documentId=doc_id, body={"requests": batch2}
            ).execute()

        # Step 5: Apply heading + separator styles AFTER all cell formatting.
        # Must be last because batch2's NORMAL_TEXT on cell paragraphs can
        # interfere if ranges overlap with the heading paragraph.
        if heading_text and _heading_style_reqs:
            docs_service.documents().batchUpdate(
                documentId=doc_id, body={"requests": _heading_style_reqs}
            ).execute()

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "table_inserted": True,
            "rows": num_rows,
            "cols": num_cols,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def insert_section(doc_id: str, after_heading: str, md_file: str, project: str = None):
    """Insert formatted markdown content after a heading.

    Uses the converter for formatting, which means all spacing and typography
    logic (heading spaceAbove, paragraph spacing, dense section detection, etc.)
    is automatically applied.
    """
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    md_path = Path(md_file)
    if not md_path.exists():
        print(json.dumps({"error": f"File not found: {md_file}"}), file=sys.stderr)
        sys.exit(1)

    markdown_content = md_path.read_text()

    # Load converter
    portals_path = Path.home() / "Documents/Claude Code/portals/portals/adapters/gdocs/converter.py"
    if not portals_path.exists():
        print(json.dumps({"error": "Converter not found at portals/portals/adapters/gdocs/converter.py"}), file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, str(portals_path.parent.parent.parent.parent))
    from portals.adapters.gdocs.converter import GoogleDocsConverter

    converter = GoogleDocsConverter()
    conversion = converter.markdown_to_gdocs(markdown_content)
    plain_text = conversion.plain_text
    batch_requests = converter.generate_batch_requests(conversion)

    try:
        # Fetch doc, find insertion point
        doc = docs_service.documents().get(documentId=doc_id).execute()
        body = doc.get("body", {}).get("content", [])
        heading_map = _build_heading_map(body, doc.get("inlineObjects", {}))

        if after_heading not in heading_map:
            print(json.dumps({
                "error": f"Heading not found: {after_heading}",
                "available_headings": list(heading_map.keys()),
            }), file=sys.stderr)
            sys.exit(1)

        doc_end = body[-1].get("endIndex", 1) if body else 1
        insert_at = heading_map[after_heading].get("sectionEnd")
        if insert_at is None:
            insert_at = doc_end - 1
        else:
            insert_at = min(insert_at, doc_end - 1)

        # Insert separator + plain text
        text_to_insert = "\n" + plain_text
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": insert_at}, "text": text_to_insert}}]},
        ).execute()

        # Offset and apply formatting requests
        # Converter assumes text starts at index 1; we inserted at insert_at + 1
        # So offset = insert_at (converter pos 1 -> actual pos insert_at + 1)
        content_start = insert_at + 1
        content_end = insert_at + 1 + len(plain_text)

        # Reset entire range to NORMAL_TEXT first - inserted text inherits
        # the surrounding paragraph's style (including heading spacing).
        # Must clear spaceAbove/Below too, not just namedStyleType, because
        # explicit spacing overrides persist even after style type change.
        # Include the separator \n paragraph (at insert_at) in the reset.
        reset_req = {
            "updateParagraphStyle": {
                "range": {"startIndex": insert_at, "endIndex": content_end},
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    "spaceAbove": {"magnitude": 0, "unit": "PT"},
                    "spaceBelow": {"magnitude": 0, "unit": "PT"},
                },
                "fields": "namedStyleType,spaceAbove,spaceBelow",
            }
        }

        if batch_requests:
            offset = insert_at
            offset_reqs = json.loads(json.dumps(batch_requests))  # deep copy via JSON round-trip
            _offset_indices(offset_reqs, offset)
            all_reqs = [reset_req] + offset_reqs
        else:
            all_reqs = [reset_req]

        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": all_reqs},
        ).execute()

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "section_inserted": True,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def list_tabs(doc_id: str, project: str = None):
    """List all root-level tabs in a document."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        doc = docs_service.documents().get(
            documentId=doc_id, includeTabsContent=True
        ).execute()

        tabs = []
        for tab in doc.get("tabs", []):
            props = tab.get("tabProperties", {})
            tab_info = {
                "tabId": props.get("tabId"),
                "title": props.get("title"),
                "index": props.get("index"),
            }
            # Include optional fields only when present
            if "parentTabId" in props:
                tab_info["parentTabId"] = props["parentTabId"]
            if "nestingLevel" in props:
                tab_info["nestingLevel"] = props["nestingLevel"]
            if props.get("tabIconEmoji"):
                tab_info["iconEmoji"] = props["tabIconEmoji"]
            tabs.append(tab_info)

        print(json.dumps({
            "document_id": doc_id,
            "title": doc.get("title"),
            "tab_count": len(tabs),
            "tabs": tabs,
            "project": resolved_project
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def create_tab(doc_id: str, title: str, project: str = None):
    """Create a new tab in a document."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        result = docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"addDocumentTab": {"tabProperties": {"title": title}}}]}
        ).execute()

        # Extract new tab ID from reply
        replies = result.get("replies", [])
        new_tab_id = None
        if replies:
            new_tab_id = (
                replies[0]
                .get("addDocumentTab", {})
                .get("tabProperties", {})
                .get("tabId")
            )

        # If expected path didn't work, print raw reply for diagnosis
        if not new_tab_id:
            print(json.dumps({
                "warning": "Could not extract tabId from expected path replies[0].addDocumentTab.tabProperties.tabId",
                "raw_replies": replies
            }), file=sys.stderr)

        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        if new_tab_id:
            doc_url += f"#tab={new_tab_id}"

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "tab_id": new_tab_id,
            "title": title,
            "url": doc_url,
            "project": resolved_project
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def delete_tab(doc_id: str, tab_id: str, project: str = None):
    """Delete a tab from a document. Child tabs are deleted as well."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"deleteTab": {"tabId": tab_id}}]}
        ).execute()

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "deleted_tab_id": tab_id,
            "project": resolved_project
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def create_tab_from_markdown(doc_id: str, title: str, markdown_file: str, project: str = None):
    """Create a new tab and populate it with formatted markdown content."""
    # Validate markdown file exists BEFORE creating tab (no orphaned empty tabs)
    md_path = Path(markdown_file)
    if not md_path.exists():
        print(json.dumps({"error": f"File not found: {markdown_file}"}), file=sys.stderr)
        sys.exit(1)

    resolved_project = _resolve_project(project, markdown_file)
    docs_service = get_docs_service(project=resolved_project)

    try:
        # Create the tab
        result = docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"addDocumentTab": {"tabProperties": {"title": title}}}]}
        ).execute()

        replies = result.get("replies", [])
        new_tab_id = None
        if replies:
            new_tab_id = (
                replies[0]
                .get("addDocumentTab", {})
                .get("tabProperties", {})
                .get("tabId")
            )

        if not new_tab_id:
            print(json.dumps({
                "error": "Tab created but could not extract tabId",
                "raw_replies": replies
            }), file=sys.stderr)
            sys.exit(1)

        # Populate the tab with markdown content using existing update logic
        update_from_markdown(doc_id, markdown_file, resolved_project, tab_id=new_tab_id)

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def update_from_markdown(doc_id: str, markdown_file: str, project: str = None, tab_id: str = None, skip_title_sync: bool = False):
    """
    Update existing document with new markdown content.
    Clears existing content and replaces with formatted markdown.
    Supports targeting a specific tab via tab_id.
    """
    resolved_project = _resolve_project(project, markdown_file)
    docs_service = get_docs_service(project=resolved_project)

    # Read markdown file
    md_path = Path(markdown_file)
    if not md_path.exists():
        print(json.dumps({"error": f"File not found: {markdown_file}"}), file=sys.stderr)
        sys.exit(1)

    markdown_content = md_path.read_text()

    # Helper to build location dict with optional tabId
    def _loc(index):
        loc = {"index": index}
        if tab_id:
            loc["tabId"] = tab_id
        return loc

    def _range(start, end):
        r = {"startIndex": start, "endIndex": end}
        if tab_id:
            r["tabId"] = tab_id
        return r

    try:
        # Get current document to find content range
        if tab_id:
            doc = docs_service.documents().get(
                documentId=doc_id, includeTabsContent=True
            ).execute()
            # Find the target tab's body
            body = []
            for tab in doc.get("tabs", []):
                if tab.get("tabProperties", {}).get("tabId") == tab_id:
                    body = tab.get("documentTab", {}).get("body", {}).get("content", [])
                    break
        else:
            doc = docs_service.documents().get(documentId=doc_id).execute()
            body = doc.get("body", {}).get("content", [])

        # Find end index (clear everything except the trailing newline)
        end_index = 1
        if body:
            last_element = body[-1]
            end_index = last_element.get("endIndex", 2) - 1

        # Delete existing content (if any)
        if end_index > 1:
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"deleteContentRange": {"range": _range(1, end_index)}}]}
            ).execute()

        # Use portals converter for formatting
        portals_converter_path = Path.home() / "Documents/Claude Code/portals/portals/adapters/gdocs/converter.py"

        if portals_converter_path.exists():
            sys.path.insert(0, str(portals_converter_path.parent.parent.parent.parent))
            from portals.adapters.gdocs.converter import GoogleDocsConverter

            converter = GoogleDocsConverter()
            conversion = converter.markdown_to_gdocs(markdown_content)
            plain_text = conversion.plain_text
            batch_requests = converter.generate_batch_requests(conversion)

            # Inject tabId into all batch requests if targeting a tab
            # Only Location and Range types accept tabId. TableCellLocation does NOT.
            if tab_id and batch_requests:
                TABID_FIELDS = {"location", "range", "tableStartLocation"}

                def _inject_tab_id(obj):
                    """Recursively inject tabId into Location/Range fields only."""
                    if isinstance(obj, dict):
                        for key in TABID_FIELDS:
                            if key in obj and isinstance(obj[key], dict):
                                if "tabId" not in obj[key]:
                                    obj[key]["tabId"] = tab_id
                        for v in obj.values():
                            _inject_tab_id(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            _inject_tab_id(item)

                for req in batch_requests:
                    _inject_tab_id(req)

            # Insert plain text
            if plain_text:
                docs_service.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": [{"insertText": {"location": _loc(1), "text": plain_text}}]}
                ).execute()

            # Apply formatting
            if batch_requests:
                docs_service.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": batch_requests}
                ).execute()

            # Sync document title from h1 heading (only for default tab)
            title_updated = False
            if not tab_id and not skip_title_sync:
                for fmt in conversion.format_ranges:
                    if fmt.format_type == "heading" and fmt.level == 1 and fmt.text:
                        drive_service = get_drive_service(project=resolved_project)
                        drive_service.files().update(
                            fileId=doc_id,
                            body={"name": fmt.text}
                        ).execute()
                        title_updated = True
                        break

            formatted = True
        else:
            # Fallback: plain text
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": _loc(1), "text": markdown_content}}]}
            ).execute()
            formatted = False
            title_updated = False

        result = {
            "success": True,
            "document_id": doc_id,
            "title": doc.get("title"),
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "formatted": formatted,
            "title_synced": title_updated,
            "project": resolved_project,
            "account": PROJECT_CONFIG[resolved_project]["impersonate_user"],
            "action": "updated"
        }
        if tab_id:
            result["tab_id"] = tab_id
        print(json.dumps(result, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


# =============================================================================
# PDF export
# =============================================================================

def export_pdf_doc(doc_id: str, output_path: str = None, project: str = None):
    """Export a Google Doc as PDF."""
    resolved_project = _resolve_project(project)
    drive_service = get_drive_service(project=resolved_project)

    try:
        content = drive_service.files().export_media(
            fileId=doc_id, mimeType="application/pdf"
        ).execute()

        if output_path:
            path = Path(output_path)
        else:
            path = Path(f"/tmp/doc_{doc_id[:8]}.pdf")

        path.write_bytes(content)

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "path": str(path),
            "size_bytes": len(content),
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "project": resolved_project,
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Unified Google Docs operations (multi-project)")

    # Operation flags
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create new document")
    group.add_argument("--create-md", action="store_true", help="Create from markdown with formatting")
    group.add_argument("--read", metavar="DOC_ID", help="Read document content")
    group.add_argument("--structure", metavar="DOC_ID", help="Get document structure")
    group.add_argument("--append", metavar="DOC_ID", help="Append text to document")
    group.add_argument("--batch", metavar="DOC_ID", help="Apply batch updates")
    group.add_argument("--update-md", metavar="DOC_ID", help="Update existing doc with markdown")
    group.add_argument("--list-tabs", metavar="DOC_ID", help="List tabs in a document")
    group.add_argument("--create-tab", metavar="DOC_ID", help="Create a new tab (requires --title)")
    group.add_argument("--create-tab-md", metavar="DOC_ID", help="Create tab with markdown content (requires --title and --file)")
    group.add_argument("--delete-tab", metavar="DOC_ID", help="Delete a tab (requires --tab)")
    group.add_argument("--inspect", metavar="DOC_ID", help="Inspect document structure with indices and heading map")
    group.add_argument("--patch", metavar="DOC_ID", help="Apply targeted edits from operations file (requires --ops)")
    group.add_argument("--insert-table", metavar="DOC_ID", help="Insert formatted table after a heading")
    group.add_argument("--insert-section", metavar="DOC_ID", help="Insert formatted markdown section after a heading")
    group.add_argument("--export-pdf", metavar="DOC_ID", help="Export document as PDF")
    group.add_argument("--find-text", metavar="DOC_ID", help="Find exact text and return character index ranges (requires --text)")
    group.add_argument("--find-heading", metavar="DOC_ID", help="Find heading by name and return section boundaries (requires --text)")
    group.add_argument("--check-comments", metavar="DOC_ID", help="List all comment anchors with quoted content (read-only)")

    # Options
    parser.add_argument("--title", help="Document title")
    parser.add_argument("--content", help="Initial content")
    parser.add_argument("--file", help="Markdown file path")
    parser.add_argument("--folder", help="Folder ID to create document in")
    parser.add_argument("--text", help="Text to append")
    parser.add_argument("--requests", help="JSON batch update requests")
    parser.add_argument("--project", "-p", help="Project (anaro-labs, estate-mate). Auto-detected from --file path if not specified.")
    parser.add_argument("--tab", help="Target a specific tab by tabId (for --update-md, --append, --read)")
    parser.add_argument("--skip-title-sync", action="store_true", help="Don't sync Drive filename from h1")
    parser.add_argument("--ops", help="Operations JSON file for --patch")
    parser.add_argument("--after-heading", help="Target heading for --insert-table and --insert-section")
    parser.add_argument("--heading", help="Optional heading text to insert before table")
    parser.add_argument("--output", help="Output file path for --export-pdf")

    args = parser.parse_args()

    if args.create:
        if not args.title:
            parser.error("--create requires --title")
        create_document(args.title, args.content, args.folder, args.project, args.file)

    elif args.create_md:
        if not args.title or not args.file:
            parser.error("--create-md requires --title and --file")
        create_from_markdown(args.title, args.file, args.folder, args.project)

    elif args.read:
        read_document(args.read, args.project, args.tab)

    elif args.structure:
        get_structure(args.structure, args.project)

    elif args.append:
        if not args.text:
            parser.error("--append requires --text")
        append_text(args.append, args.text, args.project)

    elif args.batch:
        if not args.requests:
            parser.error("--batch requires --requests")
        batch_update(args.batch, args.requests, args.project)

    elif args.update_md:
        if not args.file:
            parser.error("--update-md requires --file")
        update_from_markdown(args.update_md, args.file, args.project, args.tab, args.skip_title_sync)

    elif args.list_tabs:
        list_tabs(args.list_tabs, args.project)

    elif args.create_tab:
        if not args.title:
            parser.error("--create-tab requires --title")
        create_tab(args.create_tab, args.title, args.project)

    elif args.create_tab_md:
        if not args.title or not args.file:
            parser.error("--create-tab-md requires --title and --file")
        create_tab_from_markdown(args.create_tab_md, args.title, args.file, args.project)

    elif args.delete_tab:
        if not args.tab:
            parser.error("--delete-tab requires --tab")
        delete_tab(args.delete_tab, args.tab, args.project)

    elif args.inspect:
        inspect_document(args.inspect, args.project)

    elif args.patch:
        if not args.ops:
            parser.error("--patch requires --ops")
        patch_document(args.patch, args.ops, args.project)

    elif args.insert_table:
        if not args.after_heading or not args.file:
            parser.error("--insert-table requires --after-heading and --file")
        insert_table(args.insert_table, args.after_heading, args.file, args.heading, args.project)

    elif args.insert_section:
        if not args.after_heading or not args.file:
            parser.error("--insert-section requires --after-heading and --file")
        insert_section(args.insert_section, args.after_heading, args.file, args.project)

    elif args.export_pdf:
        export_pdf_doc(args.export_pdf, output_path=args.output, project=args.project)

    elif args.find_text:
        if not args.text:
            parser.error("--find-text requires --text")
        find_text(args.find_text, args.text, args.project)

    elif args.find_heading:
        if not args.text:
            parser.error("--find-heading requires --text")
        find_heading(args.find_heading, args.text, args.project)

    elif args.check_comments:
        check_comments(args.check_comments, args.project)


if __name__ == "__main__":
    main()
