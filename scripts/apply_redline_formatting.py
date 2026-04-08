"""
Apply redline formatting to a Google Doc containing «DEL»...«/DEL» and «ADD»...«/ADD» markers.

Converts markers to visual formatting:
- DEL content: strikethrough + gray foreground (rgb 0.5, 0.5, 0.5)
- ADD content: green foreground (rgb 0, 0.4, 0)
Then removes the marker tags themselves.

Two-phase approach:
  Phase 1: Apply formatting (updateTextStyle - doesn't shift indices)
  Phase 2: Delete markers in reverse index order (highest first)

Usage:
    uv run python scripts/apply_redline_formatting.py DOC_ID [--project anaro-labs|estate-mate] [--dry-run]
"""

import argparse
import json
import re
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SERVICE_ACCOUNTS = {
    "anaro-labs": str(Path.home() / ".google_workspace_mcp/claude-code-api-482615-91dc5f2fd86b.json"),
    "estate-mate": str(Path.home() / ".google_workspace_estatemate/service-account.json"),
}

SCOPES = ["https://www.googleapis.com/auth/documents"]

# Marker lengths in characters
OPEN_TAG_LEN = 5   # «DEL» or «ADD»
CLOSE_DEL_LEN = 6  # «/DEL»
CLOSE_ADD_LEN = 6  # «/ADD»


def get_docs_service(project: str):
    cred_path = SERVICE_ACCOUNTS[project]
    creds = service_account.Credentials.from_service_account_file(cred_path, scopes=SCOPES)
    if project == "anaro-labs":
        creds = creds.with_subject("roman@anarolabs.com")
    elif project == "estate-mate":
        creds = creds.with_subject("roman@estatemate.io")
    return build("docs", "v1", credentials=creds)


def extract_full_text(doc):
    """Walk document body and build full text with index positions.

    Returns:
        full_text: concatenated text from all text runs
        char_to_doc_index: list mapping string position -> Google Docs API index
    """
    text_parts = []
    body = doc.get("body", {})
    content = body.get("content", [])

    def walk_content(content_list):
        for element in content_list:
            if "paragraph" in element:
                for elem in element["paragraph"].get("elements", []):
                    if "textRun" in elem:
                        start = elem["startIndex"]
                        text = elem["textRun"]["content"]
                        text_parts.append((start, text))
            elif "table" in element:
                for row in element["table"].get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        walk_content(cell.get("content", []))
            elif "tableOfContents" in element:
                walk_content(element["tableOfContents"].get("content", []))
            elif "sectionBreak" in element:
                pass  # no text

    walk_content(content)

    # Sort by start index
    text_parts.sort(key=lambda x: x[0])

    # Build full text and char-to-index mapping
    full_text = ""
    char_to_doc_index = []

    for start, text in text_parts:
        for i, ch in enumerate(text):
            char_to_doc_index.append(start + i)
        full_text += text

    return full_text, char_to_doc_index


def find_markers(full_text):
    """Find all «DEL»...«/DEL» and «ADD»...«/ADD» ranges."""
    markers = []

    for m in re.finditer(r'\u00abDEL\u00bb(.*?)\u00ab/DEL\u00bb', full_text, re.DOTALL):
        markers.append({
            "type": "DEL",
            "open_start": m.start(),                  # start of «DEL»
            "open_end": m.start() + OPEN_TAG_LEN,     # end of «DEL»
            "content_start": m.start() + OPEN_TAG_LEN,
            "content_end": m.end() - CLOSE_DEL_LEN,
            "close_start": m.end() - CLOSE_DEL_LEN,   # start of «/DEL»
            "close_end": m.end(),                      # end of «/DEL»
            "content": m.group(1),
        })

    for m in re.finditer(r'\u00abADD\u00bb(.*?)\u00ab/ADD\u00bb', full_text, re.DOTALL):
        markers.append({
            "type": "ADD",
            "open_start": m.start(),
            "open_end": m.start() + OPEN_TAG_LEN,
            "content_start": m.start() + OPEN_TAG_LEN,
            "content_end": m.end() - CLOSE_ADD_LEN,
            "close_start": m.end() - CLOSE_ADD_LEN,
            "close_end": m.end(),
            "content": m.group(1),
        })

    markers.sort(key=lambda x: x["open_start"])
    return markers


def build_format_requests(markers, idx):
    """Phase 1: formatting requests (don't shift indices)."""
    requests = []
    for m in markers:
        content_start_idx = idx[m["content_start"]]
        content_end_idx = idx[m["close_start"] - 1] + 1 if m["content_end"] > m["content_start"] else idx[m["content_start"]]

        if content_start_idx >= content_end_idx:
            continue  # empty content

        if m["type"] == "DEL":
            requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": content_start_idx,
                        "endIndex": content_end_idx,
                    },
                    "textStyle": {
                        "strikethrough": True,
                        "foregroundColor": {
                            "color": {
                                "rgbColor": {"red": 0.5, "green": 0.5, "blue": 0.5}
                            }
                        },
                    },
                    "fields": "strikethrough,foregroundColor",
                }
            })
        elif m["type"] == "ADD":
            requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": content_start_idx,
                        "endIndex": content_end_idx,
                    },
                    "textStyle": {
                        "foregroundColor": {
                            "color": {
                                "rgbColor": {"red": 0.0, "green": 0.4, "blue": 0.0}
                            }
                        },
                    },
                    "fields": "foregroundColor",
                }
            })
    return requests


def build_delete_requests(markers, idx):
    """Phase 2: deletion requests in reverse order (highest index first).

    For each marker, delete closing tag first, then opening tag.
    Process markers from last to first to avoid index shifting issues.
    """
    # Collect all tag ranges to delete
    deletions = []
    for m in markers:
        # Closing tag range
        close_start_idx = idx[m["close_start"]]
        close_end_idx = idx[m["close_end"] - 1] + 1
        deletions.append((close_start_idx, close_end_idx))

        # Opening tag range
        open_start_idx = idx[m["open_start"]]
        open_end_idx = idx[m["open_end"] - 1] + 1
        deletions.append((open_start_idx, open_end_idx))

    # Sort by start index DESCENDING (delete from end of document first)
    deletions.sort(key=lambda x: x[0], reverse=True)

    requests = []
    for start, end in deletions:
        requests.append({
            "deleteContentRange": {
                "range": {
                    "startIndex": start,
                    "endIndex": end,
                }
            }
        })
    return requests


def main():
    parser = argparse.ArgumentParser(description="Apply redline formatting to Google Doc")
    parser.add_argument("doc_id", help="Google Doc ID")
    parser.add_argument("--project", default="anaro-labs", choices=["anaro-labs", "estate-mate"])
    parser.add_argument("--dry-run", action="store_true", help="Print requests without applying")
    args = parser.parse_args()

    print(f"Connecting to Google Docs API ({args.project})...")
    service = get_docs_service(args.project)

    print(f"Reading document {args.doc_id}...")
    doc = service.documents().get(documentId=args.doc_id).execute()
    title = doc.get("title", "Untitled")
    print(f"Document: {title}")

    print("Extracting text and building index map...")
    full_text, char_to_doc_index = extract_full_text(doc)
    print(f"Total text length: {len(full_text)} chars, {len(char_to_doc_index)} index entries")

    print("Finding markers...")
    markers = find_markers(full_text)
    del_count = sum(1 for m in markers if m["type"] == "DEL")
    add_count = sum(1 for m in markers if m["type"] == "ADD")
    print(f"Found {len(markers)} markers: {del_count} DEL, {add_count} ADD")

    if not markers:
        print("No markers found. Nothing to do.")
        return

    for i, m in enumerate(markers):
        preview = m["content"][:60].replace("\n", "\\n")
        suffix = "..." if len(m["content"]) > 60 else ""
        print(f"  [{i+1}] {m['type']}: \"{preview}{suffix}\"")

    # Phase 1: formatting
    print("\nPhase 1: Building formatting requests...")
    format_requests = build_format_requests(markers, char_to_doc_index)
    print(f"  {len(format_requests)} formatting requests")

    # Phase 2: deletions
    print("Phase 2: Building deletion requests (reverse order)...")
    delete_requests = build_delete_requests(markers, char_to_doc_index)
    print(f"  {len(delete_requests)} deletion requests")

    if args.dry_run:
        print("\n--- DRY RUN ---")
        print(f"Phase 1 sample ({len(format_requests)} total):")
        for r in format_requests[:3]:
            rng = r["updateTextStyle"]["range"]
            print(f"  format {rng['startIndex']}-{rng['endIndex']}")
        print(f"Phase 2 sample ({len(delete_requests)} total):")
        for r in delete_requests[:6]:
            rng = r["deleteContentRange"]["range"]
            print(f"  delete {rng['startIndex']}-{rng['endIndex']}")
        return

    # Apply Phase 1
    if format_requests:
        print("\nApplying Phase 1: formatting...")
        service.documents().batchUpdate(
            documentId=args.doc_id,
            body={"requests": format_requests}
        ).execute()
        print("  Done.")

    # Apply Phase 2
    if delete_requests:
        # Split into batches if needed (API allows ~100 per batch)
        batch_size = 100
        for i in range(0, len(delete_requests), batch_size):
            batch = delete_requests[i:i + batch_size]
            print(f"Applying Phase 2 batch {i // batch_size + 1}: deleting {len(batch)} marker tags...")
            service.documents().batchUpdate(
                documentId=args.doc_id,
                body={"requests": batch}
            ).execute()
        print("  Done.")

    print(f"\nComplete. {len(markers)} redline markers formatted and stripped.")
    print(f"URL: https://docs.google.com/document/d/{args.doc_id}/edit")


if __name__ == "__main__":
    main()
