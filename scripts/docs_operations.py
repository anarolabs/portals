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


def read_document(doc_id: str, project: str = None):
    """Read document content including tables, images, and other elements."""
    resolved_project = _resolve_project(project)
    docs_service = get_docs_service(project=resolved_project)

    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()

        # Get inline objects (images, etc.) for reference
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


def update_from_markdown(doc_id: str, markdown_file: str, project: str = None):
    """
    Update existing document with new markdown content.
    Clears existing content and replaces with formatted markdown.
    """
    resolved_project = _resolve_project(project, markdown_file)
    docs_service = get_docs_service(project=resolved_project)

    # Read markdown file
    md_path = Path(markdown_file)
    if not md_path.exists():
        print(json.dumps({"error": f"File not found: {markdown_file}"}), file=sys.stderr)
        sys.exit(1)

    markdown_content = md_path.read_text()

    try:
        # Get current document to find content range
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
                body={"requests": [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end_index}}}]}
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

            # Insert plain text
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
            # Fallback: plain text
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": markdown_content}}]}
            ).execute()
            formatted = False

        print(json.dumps({
            "success": True,
            "document_id": doc_id,
            "title": doc.get("title"),
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "formatted": formatted,
            "project": resolved_project,
            "account": PROJECT_CONFIG[resolved_project]["impersonate_user"],
            "action": "updated"
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

    # Options
    parser.add_argument("--title", help="Document title")
    parser.add_argument("--content", help="Initial content")
    parser.add_argument("--file", help="Markdown file path")
    parser.add_argument("--folder", help="Folder ID to create document in")
    parser.add_argument("--text", help="Text to append")
    parser.add_argument("--requests", help="JSON batch update requests")
    parser.add_argument("--project", "-p", help="Project (anaro-labs, estate-mate). Auto-detected from --file path if not specified.")

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
        read_document(args.read, args.project)

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
        update_from_markdown(args.update_md, args.file, args.project)


if __name__ == "__main__":
    main()
