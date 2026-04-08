#!/usr/bin/env python3
"""
Google Docs edit review page - human-in-the-loop review of AI-proposed edits.

Generates a self-contained HTML file showing proposed document edits with
inline diffs, source comments, and approve/reject controls.

Usage:
    python3 docs_review.py --doc DOC_ID --manifest manifest.json --project anaro-labs --open
"""
import argparse
import difflib
import html
import json
import os
import re
import subprocess
import sys
import webbrowser
from datetime import date
from pathlib import Path


PORTALS_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent

# Import docs_operations helpers directly for single-API-call doc fetching
sys.path.insert(0, str(SCRIPTS_DIR))
from docs_operations import (
    extract_paragraph_element,
    extract_structural_element,
    extract_table,
    _build_heading_map,
)
from google_client import get_docs_service, get_drive_service, _resolve_project


# =============================================================================
# Data fetching
# =============================================================================

def _run_docs_op(args: list, project: str) -> dict:
    """Run docs_operations.py with given args and return parsed JSON."""
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "docs_operations.py"),
        *args,
        "--project", project,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PORTALS_DIR))
    if result.returncode != 0:
        print(f"Error running docs_operations.py {' '.join(args)}:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def fetch_doc_data(doc_id: str, project: str) -> dict:
    """Fetch document content, structure, and comments.

    Uses direct API call for doc content + structure (single request),
    subprocess for comments (uses Drive API).

    Returns dict with:
    - title: document title
    - content: full text (for proposal matching)
    - elements: list of {type, style, text, start, end, ...} with FULL text
    - headingMap: heading -> section boundaries
    - comments: list of active comment objects
    """
    print("Fetching document data...", file=sys.stderr)
    resolved_project = _resolve_project(project)

    # Single API call for doc content + structure
    docs_service = get_docs_service(project=resolved_project)
    doc = docs_service.documents().get(documentId=doc_id).execute()
    inline_objects = doc.get("inlineObjects", {})
    body = doc.get("body", {}).get("content", [])

    # Extract full content (for proposal matching)
    content_parts = []
    for element in body:
        content_parts.append(extract_structural_element(element, inline_objects))
    content = "".join(content_parts)
    print(f"  Content: {len(content)} chars", file=sys.stderr)

    # Extract elements with FULL text (not truncated)
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
                "text": full_text,  # FULL text, not truncated
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
                    for c in cell.get("content", []):
                        cell_text.append(extract_structural_element(c, inline_objects))
                    header_row.append("".join(cell_text).strip()[:40])

            elements.append({
                "type": "table",
                "startIndex": start_idx,
                "endIndex": end_idx,
                "rows": row_count,
                "cols": col_count,
                "headerRow": header_row,
            })

    print(f"  Elements: {len(elements)}", file=sys.stderr)

    # Build heading map
    heading_map = _build_heading_map(body, inline_objects)

    # Fetch comments via subprocess (uses Drive API, separate auth flow)
    comments_data = _run_docs_op(["--check-comments", doc_id], project)
    print(f"  Comments: {comments_data.get('totalComments', 0)} total "
          f"({comments_data.get('activeComments', 0)} active)", file=sys.stderr)

    return {
        "title": doc.get("title", "Untitled"),
        "content": content,
        "elements": elements,
        "headingMap": heading_map,
        "comments": comments_data.get("comments", []),
        "raw_body": body,
        "inline_objects": inline_objects,
    }


# =============================================================================
# Manifest loading
# =============================================================================

def load_manifest(path: str) -> dict:
    """Load and validate edit manifest JSON.

    Supports two proposal types:
    - "replace" (default): current_text → proposed_text
    - "insert": new text inserted after insert_after heading/text
    """
    with open(path) as f:
        manifest = json.load(f)

    if "proposals" not in manifest:
        print("Error: manifest must contain 'proposals' array", file=sys.stderr)
        sys.exit(1)

    for i, p in enumerate(manifest["proposals"]):
        ptype = p.get("type", "replace")
        if "id" not in p or "title" not in p:
            print(f"Error: proposal {i} missing 'id' or 'title'", file=sys.stderr)
            sys.exit(1)
        if ptype == "replace":
            if "current_text" not in p or "proposed_text" not in p:
                print(f"Error: replace proposal {p['id']} needs "
                      f"'current_text' and 'proposed_text'", file=sys.stderr)
                sys.exit(1)
        elif ptype == "insert":
            if "proposed_text" not in p or "insert_after" not in p:
                print(f"Error: insert proposal {p['id']} needs "
                      f"'proposed_text' and 'insert_after'", file=sys.stderr)
                sys.exit(1)

    return manifest


# =============================================================================
# Proposal location
# =============================================================================

def locate_proposals(content: str, proposals: list) -> list:
    """Find proposal anchors in doc content.

    For "replace" proposals: finds current_text, records char offset.
    For "insert" proposals: finds insert_after text to determine position.

    Returns enriched proposals with 'offset' and 'found' fields.
    """
    enriched = []
    used_ranges = []

    for p in proposals:
        ep = dict(p)
        ptype = p.get("type", "replace")

        if ptype == "insert":
            # Find where to insert (the text/heading to insert after)
            anchor = p.get("insert_after", "")
            idx = content.find(anchor)
            if idx == -1:
                # Try normalized whitespace
                norm_anchor = re.sub(r'\s+', ' ', anchor).strip()
                norm_content = re.sub(r'\s+', ' ', content)
                norm_idx = norm_content.find(norm_anchor)
                if norm_idx != -1:
                    idx = _map_normalized_offset(content, norm_content, norm_idx)

            if idx == -1:
                print(f"  Warning: insert anchor not found for {p['id']}: "
                      f"{anchor[:60]}...", file=sys.stderr)
                ep["found"] = False
                ep["offset"] = -1
            else:
                ep["found"] = True
                ep["offset"] = idx + len(anchor)  # Position AFTER the anchor
                ep["end_offset"] = ep["offset"]
            enriched.append(ep)
            continue

        # Replace type
        search_text = p["current_text"]

        # Try exact match first
        idx = content.find(search_text)

        # If not found, try with normalized whitespace
        if idx == -1:
            normalized_search = re.sub(r'\s+', ' ', search_text).strip()
            normalized_content = re.sub(r'\s+', ' ', content)

            norm_idx = normalized_content.find(normalized_search)
            if norm_idx != -1:
                idx = _map_normalized_offset(content, normalized_content, norm_idx)

        if idx == -1:
            print(f"  Warning: text not found for {p['id']}: "
                  f"{search_text[:60]}...", file=sys.stderr)
            ep["found"] = False
            ep["offset"] = -1
        else:
            end = idx + len(search_text)
            for (rs, re_) in used_ranges:
                if idx < re_ and end > rs:
                    print(f"  Warning: {p['id']} overlaps with another proposal "
                          f"at [{idx}:{end}]", file=sys.stderr)
                    break

            ep["found"] = True
            ep["offset"] = idx
            ep["end_offset"] = end
            used_ranges.append((idx, end))

        enriched.append(ep)

    # Sort by offset so they appear in document order
    enriched.sort(key=lambda x: x.get("offset", float("inf")))
    return enriched


def _map_normalized_offset(original: str, normalized: str, norm_offset: int) -> int:
    """Map an offset in whitespace-normalized text back to original text."""
    # Walk through original, tracking position in normalized
    norm_pos = 0
    in_whitespace = False
    for i, ch in enumerate(original):
        if norm_pos >= norm_offset:
            return i
        if ch in (' ', '\t', '\n', '\r'):
            if not in_whitespace:
                norm_pos += 1  # Single space in normalized
                in_whitespace = True
        else:
            norm_pos += 1
            in_whitespace = False
    return -1


# =============================================================================
# Word-level diff
# =============================================================================

def word_diff_html(current: str, proposed: str) -> str:
    """Word-level diff using difflib.SequenceMatcher.

    Returns HTML with <del class='diff-del'> and <ins class='diff-ins'> tags.
    """
    current_words = current.split()
    proposed_words = proposed.split()

    sm = difflib.SequenceMatcher(None, current_words, proposed_words)
    parts = []

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            parts.append(html.escape(" ".join(current_words[i1:i2])))
        elif op == "delete":
            text = html.escape(" ".join(current_words[i1:i2]))
            parts.append(f'<del class="diff-del">{text}</del>')
        elif op == "insert":
            text = html.escape(" ".join(proposed_words[j1:j2]))
            parts.append(f'<ins class="diff-ins">{text}</ins>')
        elif op == "replace":
            old_text = html.escape(" ".join(current_words[i1:i2]))
            new_text = html.escape(" ".join(proposed_words[j1:j2]))
            parts.append(f'<del class="diff-del">{old_text}</del>')
            parts.append(f'<ins class="diff-ins">{new_text}</ins>')

    return " ".join(parts)


# =============================================================================
# Document HTML rendering
# =============================================================================

def _extract_table_data(table_element, inline_objects=None):
    """Extract structured table data: list of rows, each a list of cell strings.

    Also returns flat text for proposal matching.
    """
    rows = []
    for row in table_element.get("tableRows", []):
        cells = []
        for cell in row.get("tableCells", []):
            cell_text = []
            for c in cell.get("content", []):
                cell_text.append(extract_structural_element(c, inline_objects))
            cells.append("".join(cell_text).strip().replace("\n", " "))
        rows.append(cells)

    flat_text = "\n".join(" | ".join(cells) for cells in rows)
    return rows, flat_text


def _render_text_with_comments(text: str, comment_anchors: list,
                               anchored_comment_ids: set) -> str:
    """Render paragraph text with comment highlight spans.

    Finds comment quotedText in the paragraph and wraps matching portions
    in highlight spans with comment IDs for connector line targeting.
    """
    # Find all comments whose quotedText appears in this text
    matches = []
    for c in comment_anchors:
        cid = c["id"]
        if cid in anchored_comment_ids:
            continue
        quoted = c.get("quotedText", "")
        # Use first 40 chars for matching (comments can be long)
        search = quoted[:60] if len(quoted) > 60 else quoted
        idx = text.find(search)
        if idx >= 0:
            matches.append((idx, len(search), cid))
            anchored_comment_ids.add(cid)

    if not matches:
        return html.escape(text).replace('\n', '<br>')

    # Sort matches by position, build result
    matches.sort(key=lambda x: x[0])
    result = []
    cursor = 0
    for start, length, cid in matches:
        if start < cursor:
            continue  # Skip overlapping
        result.append(html.escape(text[cursor:start]))
        matched_text = html.escape(text[start:start + length])
        result.append(
            f'<span class="comment-highlight" id="canchor-{cid}" '
            f'data-comment-id="{cid}">{matched_text}</span>'
        )
        cursor = start + length
    result.append(html.escape(text[cursor:]))
    return "".join(result).replace('\n', '<br>')


def build_document_html(content: str, elements: list, enriched_proposals: list,
                        raw_body: list = None, inline_objects: dict = None,
                        enriched_comments: list = None) -> str:
    """Render document body as HTML with headings/paragraphs.

    Insert diff markup at proposal anchor points and comment highlights.
    Elements have full text directly. For tables, extracts full table text
    to check if proposals match within table content.
    """
    # Build a list of text segments from elements, including table text
    segments = []
    table_idx = 0
    for elem in elements:
        if elem["type"] == "paragraph":
            style = elem.get("style", "NORMAL_TEXT")
            text = elem.get("text", "").strip()

            if not text:
                continue

            segments.append({
                "type": "paragraph",
                "style": style,
                "text": text,
                "listId": elem.get("listId"),
                "nestingLevel": elem.get("nestingLevel", 0),
            })
        elif elem["type"] == "table":
            # Extract full table data for rendering and proposal matching
            table_rows = []
            table_text = ""
            if raw_body:
                for body_elem in raw_body:
                    if ("table" in body_elem and
                            body_elem.get("startIndex") == elem.get("startIndex")):
                        table_rows, table_text = _extract_table_data(
                            body_elem["table"], inline_objects)
                        break
            segments.append({
                "type": "table",
                "rows": elem.get("rows", 0),
                "cols": elem.get("cols", 0),
                "tableRows": table_rows,
                "fullText": table_text,
            })

    # Split proposals by type
    replace_proposals = [p for p in enriched_proposals
                         if p.get("found") and p.get("type", "replace") == "replace"]
    insert_proposals = [p for p in enriched_proposals
                        if p.get("found") and p.get("type") == "insert"]

    # Build comment lookup for text highlighting
    comment_anchors = []
    if enriched_comments:
        for c in enriched_comments:
            if c.get("found") and c.get("quotedText"):
                comment_anchors.append(c)

    # Track which proposals/comments have been anchored
    anchored_ids = set()
    anchored_comment_ids = set()

    # Render segments to HTML
    html_parts = []
    current_list = None

    for seg in segments:
        if seg["type"] == "table":
            if current_list:
                html_parts.append("</ul>")
                current_list = None

            # Check if any unanchored replace proposal's text is in this table
            table_proposals = []
            table_text = seg.get("fullText", "")
            for p in replace_proposals:
                if p["id"] not in anchored_ids and p["current_text"] in table_text:
                    table_proposals.append(p)
                    anchored_ids.add(p["id"])

            # Check if any insert proposals target this table's content
            for p in insert_proposals:
                if p["id"] not in anchored_ids and p["insert_after"] in table_text:
                    table_proposals.append(p)
                    anchored_ids.add(p["id"])

            html_parts.append(
                _render_table_full(seg, table_proposals))

            # Check for insert proposals that go AFTER this table
            _append_insert_markers(html_parts, insert_proposals,
                                   anchored_ids, seg.get("fullText", ""),
                                   match_mode="after_table")
            continue

        style = seg["style"]
        text = seg["text"]

        # Check if any unanchored replace proposal matches this segment
        diff_html = None
        anchor_proposal = None
        for p in replace_proposals:
            if p["id"] not in anchored_ids and p["current_text"] in text:
                anchor_proposal = p
                anchored_ids.add(p["id"])
                diff_html = word_diff_html(p["current_text"], p["proposed_text"])
                break

        # Handle lists
        if seg.get("listId"):
            if current_list != seg["listId"]:
                if current_list:
                    html_parts.append("</ul>")
                html_parts.append('<ul class="doc-list">')
                current_list = seg["listId"]
            indent_class = f' style="margin-left: {seg["nestingLevel"] * 24}px"' if seg["nestingLevel"] > 0 else ""
            if anchor_proposal:
                html_parts.append(
                    f'<li{indent_class}>'
                    f'<span class="diff-anchor" id="anchor-{anchor_proposal["id"]}" '
                    f'data-proposal-id="{anchor_proposal["id"]}">'
                    f'{diff_html}</span></li>'
                )
            else:
                rendered = _render_text_with_comments(text, comment_anchors, anchored_comment_ids)
                html_parts.append(f'<li{indent_class}>{rendered}</li>')
            continue

        # Close any open list
        if current_list:
            html_parts.append("</ul>")
            current_list = None

        # Map heading styles to HTML tags
        tag = "p"
        css_class = "doc-para"
        if style == "HEADING_1":
            tag = "h1"
            css_class = "doc-h1"
        elif style == "HEADING_2":
            tag = "h2"
            css_class = "doc-h2"
        elif style == "HEADING_3":
            tag = "h3"
            css_class = "doc-h3"
        elif style == "HEADING_4":
            tag = "h4"
            css_class = "doc-h4"
        elif style == "HEADING_5":
            tag = "h5"
            css_class = "doc-h5"
        elif style == "HEADING_6":
            tag = "h6"
            css_class = "doc-h6"
        elif style == "TITLE":
            tag = "h1"
            css_class = "doc-title"
        elif style == "SUBTITLE":
            tag = "h2"
            css_class = "doc-subtitle"

        if anchor_proposal:
            html_parts.append(
                f'<{tag} class="{css_class}">'
                f'<span class="diff-anchor" id="anchor-{anchor_proposal["id"]}" '
                f'data-proposal-id="{anchor_proposal["id"]}">'
                f'{diff_html}</span>'
                f'</{tag}>'
            )
        else:
            # Check for comment anchors in this text
            rendered = _render_text_with_comments(text, comment_anchors, anchored_comment_ids)
            html_parts.append(f'<{tag} class="{css_class}">{rendered}</{tag}>')

        # Check for insert proposals that target this paragraph/heading
        for p in insert_proposals:
            if p["id"] not in anchored_ids and p["insert_after"] in text:
                anchored_ids.add(p["id"])
                html_parts.append(_render_insert_marker(p))

    if current_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _render_table_full(seg: dict, table_proposals: list = None) -> str:
    """Render a full HTML table with diff highlights in matching cells.

    Proposals matching cell text get inline diff markup directly in the cell.
    Insert proposals render as extra rows at the bottom.
    """
    table_rows = seg.get("tableRows", [])
    if not table_rows:
        # Fallback if no row data
        return f'<div class="doc-table-placeholder">Table {seg["rows"]}x{seg["cols"]}</div>'

    # Build proposal lookup for cell matching
    replace_map = {}  # current_text -> proposal
    insert_list = []
    if table_proposals:
        for p in table_proposals:
            if p.get("type") == "insert":
                insert_list.append(p)
            else:
                replace_map[p.get("current_text", "")] = p

    rows_html = []
    for row_idx, cells in enumerate(table_rows):
        cells_html = []
        tag = "th" if row_idx == 0 else "td"
        for cell_text in cells:
            # Check if any proposal matches this cell's text
            matched_proposal = None
            for search_text, p in replace_map.items():
                if search_text in cell_text:
                    matched_proposal = p
                    break

            if matched_proposal:
                # Replace the matched portion with diff markup
                before = cell_text[:cell_text.find(matched_proposal["current_text"])]
                after = cell_text[cell_text.find(matched_proposal["current_text"])
                                  + len(matched_proposal["current_text"]):]
                diff = word_diff_html(
                    matched_proposal["current_text"],
                    matched_proposal["proposed_text"])
                cell_content = (
                    f'{html.escape(before)}'
                    f'<span class="diff-anchor" id="anchor-{matched_proposal["id"]}" '
                    f'data-proposal-id="{matched_proposal["id"]}">'
                    f'{diff}</span>'
                    f'{html.escape(after)}'
                )
                cells_html.append(f'<{tag} class="cell-has-diff">{cell_content}</{tag}>')
                # Remove from map so it doesn't match again
                del replace_map[matched_proposal["current_text"]]
            else:
                cells_html.append(f'<{tag}>{html.escape(cell_text)}</{tag}>')

        rows_html.append(f'<tr>{"".join(cells_html)}</tr>')

    # Add insert proposal rows at the bottom
    for p in insert_list:
        proposed = html.escape(p["proposed_text"])
        col_count = len(table_rows[0]) if table_rows else 1
        rows_html.append(
            f'<tr class="insert-row">'
            f'<td colspan="{col_count}">'
            f'<span class="diff-anchor" id="anchor-{p["id"]}" '
            f'data-proposal-id="{p["id"]}">'
            f'<span class="insert-label">INSERT ROW</span> '
            f'<ins class="diff-ins">{proposed}</ins>'
            f'</span></td></tr>'
        )

    return (
        f'<div class="doc-table-wrapper">'
        f'<table class="doc-table">{"".join(rows_html)}</table>'
        f'</div>'
    )


def _render_insert_marker(proposal: dict) -> str:
    """Render an insertion marker below a paragraph/heading."""
    proposed = html.escape(proposal["proposed_text"]).replace('\n', '<br>')
    return (
        f'<div class="insert-marker">'
        f'<span class="diff-anchor" id="anchor-{proposal["id"]}" '
        f'data-proposal-id="{proposal["id"]}">'
        f'<span class="insert-label">INSERT</span> '
        f'<ins class="diff-ins">{proposed}</ins>'
        f'</span></div>'
    )


def _append_insert_markers(html_parts, insert_proposals, anchored_ids,
                           text, match_mode="after_table"):
    """Check for insert proposals targeting text and append markers."""
    for p in insert_proposals:
        if p["id"] not in anchored_ids and p["insert_after"] in text:
            anchored_ids.add(p["id"])
            html_parts.append(_render_insert_marker(p))


# =============================================================================
# Comment panel (left side)
# =============================================================================

def locate_comments(content: str, elements: list, comments: list) -> list:
    """Find where each comment's quotedText appears in the document.

    Returns enriched comments with 'found' flag and location info.
    """
    enriched = []
    for c in comments:
        ec = dict(c)
        quoted = c.get("quotedText", "").strip()
        if not quoted:
            ec["found"] = False
            enriched.append(ec)
            continue

        idx = content.find(quoted)
        if idx == -1:
            # Try first 40 chars as fallback
            short = quoted[:40]
            idx = content.find(short)

        ec["found"] = idx >= 0
        ec["offset"] = idx if idx >= 0 else -1
        enriched.append(ec)

    # Sort by document position
    enriched.sort(key=lambda x: x.get("offset", float("inf")))
    return enriched


def build_comments_panel_html(comments: list) -> str:
    """Render comment thread cards for the left panel."""
    cards = []
    for c in comments:
        cid = c["id"]
        author = html.escape(c.get("author", "Unknown"))
        content_text = html.escape(c.get("content", ""))
        quoted = html.escape(c.get("quotedText", ""))
        found = c.get("found", False)

        # Replies
        replies_html = ""
        replies = c.get("replies", [])
        if replies:
            reply_items = []
            for r in replies:
                r_author = html.escape(r.get("author", ""))
                r_content = html.escape(r.get("content", ""))
                reply_items.append(
                    f'<div class="comment-reply">'
                    f'<span class="reply-author">{r_author}:</span> '
                    f'{r_content}</div>'
                )
            replies_html = f'<div class="comment-replies">{"".join(reply_items)}</div>'

        quoted_html = (
            f'<div class="comment-anchor-text">&ldquo;{quoted[:80]}'
            f'{"..." if len(quoted) > 80 else ""}&rdquo;</div>'
            if quoted else ""
        )

        indicator = "no-anchor" if not found else "has-anchor"
        resolved_cls = " resolved" if c.get("resolved") else ""

        card = (
            f'<div class="comment-card {indicator}{resolved_cls}" data-comment-id="{cid}" '
            f'id="ccard-{cid}" onclick="scrollToCommentAnchor(\'{cid}\')">'
            f'<div class="comment-card-author">{author}'
            f'{"<span class=\"resolved-badge\">resolved</span>" if c.get("resolved") else ""}'
            f'</div>'
            f'{quoted_html}'
            f'<div class="comment-card-content">{content_text}</div>'
            f'{replies_html}'
            f'</div>'
        )
        cards.append(card)

    return "\n".join(cards)


# =============================================================================
# Margin cards (right side)
# =============================================================================

def build_cards_html(proposals: list, comments: list) -> str:
    """Render margin cards for the right panel.

    Each card: title, source comments (collapsible), rationale,
    approve/reject buttons, hidden edit textarea.
    """
    # Build comment lookup
    comment_map = {c["id"]: c for c in comments}

    cards = []
    for p in proposals:
        pid = p["id"]
        found = p.get("found", False)

        # Source comments
        source_ids = p.get("source_comment_ids", [])
        comments_html = ""
        if source_ids:
            comment_items = []
            for cid in source_ids:
                c = comment_map.get(cid)
                if c:
                    author = html.escape(c.get("author", "Unknown"))
                    content = html.escape(c.get("content", ""))
                    quoted = html.escape(c.get("quotedText", ""))
                    quoted_html = (
                        f'<div class="comment-quoted">&ldquo;{quoted}&rdquo;</div>'
                        if quoted else ""
                    )
                    comment_items.append(
                        f'<div class="source-comment">'
                        f'<div class="comment-author">{author}</div>'
                        f'{quoted_html}'
                        f'<div class="comment-content">{content}</div>'
                        f'</div>'
                    )
                else:
                    comment_items.append(
                        f'<div class="source-comment">'
                        f'<div class="comment-content comment-missing">Comment {html.escape(cid)} not found</div>'
                        f'</div>'
                    )

            comments_html = (
                f'<details class="source-comments">'
                f'<summary>{len(source_ids)} source comment{"s" if len(source_ids) != 1 else ""}</summary>'
                f'{"".join(comment_items)}'
                f'</details>'
            )

        # Rationale
        rationale = html.escape(p.get("rationale", ""))
        rationale_html = f'<div class="card-rationale">{rationale}</div>' if rationale else ""

        # Section target
        section = html.escape(p.get("target_section", ""))
        section_html = f'<div class="card-section">{section}</div>' if section else ""

        # Not-found warning
        warning_html = ""
        if not found:
            warning_html = '<div class="card-warning">Text not found in document - stale manifest?</div>'

        ptype = p.get("type", "replace")
        type_badge = ""
        if ptype == "insert":
            type_badge = '<span class="card-type-badge insert">INSERT</span>'
        elif not found and ptype == "replace":
            type_badge = '<span class="card-type-badge stale">NO ANCHOR</span>'

        card = (
            f'<div class="proposal-card" data-proposal-id="{pid}" id="card-{pid}">'
            f'<div class="card-header">'
            f'<span class="card-id">{html.escape(pid)}</span>'
            f'{type_badge}'
            f'<span class="card-title">{html.escape(p["title"])}</span>'
            f'<span class="card-status-badge" id="status-{pid}">pending</span>'
            f'</div>'
            f'{section_html}'
            f'{warning_html}'
            f'{rationale_html}'
            f'{comments_html}'
            f'<div class="card-actions">'
            f'<button class="btn-approve" onclick="setStatus(\'{pid}\', \'approved\')" '
            f'title="Approve (A)">Approve</button>'
            f'<button class="btn-reject" onclick="setStatus(\'{pid}\', \'rejected\')" '
            f'title="Reject (R)">Reject</button>'
            f'<button class="btn-edit" onclick="toggleEdit(\'{pid}\')" '
            f'title="Edit (E)">Edit</button>'
            f'</div>'
            f'<div class="card-edit-area" id="edit-{pid}" style="display:none">'
            f'<textarea id="edit-text-{pid}" rows="4">{html.escape(p["proposed_text"])}</textarea>'
            f'<button class="btn-save-edit" onclick="saveEdit(\'{pid}\')">Save edit</button>'
            f'</div>'
            f'</div>'
        )
        cards.append(card)

    return "\n".join(cards)


# =============================================================================
# Full HTML generation
# =============================================================================

def generate_html(doc_title: str, document_html: str, cards_html: str,
                  proposals_json: str, proposal_count: int,
                  comments_panel_html: str = "", comment_count: int = 0,
                  saved_decisions_json: str = "{}") -> str:
    """Assemble final HTML with all CSS/JS inline.

    Embed proposals as JSON in a <script> tag for JS interactivity.
    """
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Edit review: {html.escape(doc_title)}</title>
<style>
:root {{
  --bg-page: #ffffff;
  --bg-margin: #f8f9fa;
  --bg-card: #ffffff;
  --bg-header: #f8f9fa;
  --text-primary: #1a1a1a;
  --text-secondary: #555;
  --text-muted: #888;
  --border-color: #e0e0e0;
  --border-light: #f0f0f0;
  --diff-del-bg: #fdd;
  --diff-del-text: #b30000;
  --diff-ins-bg: #dfd;
  --diff-ins-text: #006600;
  --approve-green: #2e7d32;
  --approve-green-bg: #e8f5e9;
  --approve-green-border: #a5d6a7;
  --reject-red: #c62828;
  --reject-red-bg: #ffebee;
  --reject-red-border: #ef9a9a;
  --pending-color: #f57f17;
  --pending-bg: #fff8e1;
  --connector-color: #bbb;
  --focus-blue: #1976d2;
  --focus-blue-bg: #e3f2fd;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

html, body {{
  height: 100%;
  overflow: hidden;
  background: var(--bg-page);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}}

/* Layout */
.app {{
  display: grid;
  grid-template-rows: auto 1fr auto;
  height: 100vh;
}}

/* Header */
.header {{
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-header);
  gap: 16px;
  z-index: 10;
}}

.header-left {{
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}}

.header-icon {{
  width: 28px; height: 28px;
  border-radius: 6px;
  background: var(--focus-blue);
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 700; font-size: 13px;
  flex-shrink: 0;
}}

.header-title {{
  font-weight: 600;
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}

.header-meta {{
  font-size: 12px;
  color: var(--text-muted);
}}

.header-controls {{
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}}

.kbd {{
  display: inline-block;
  font-family: monospace;
  font-size: 10px;
  padding: 1px 5px;
  border: 1px solid var(--border-color);
  border-radius: 3px;
  background: #f5f5f5;
  color: var(--text-secondary);
  vertical-align: middle;
}}

.shortcuts-hint {{
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  gap: 8px;
  align-items: center;
}}

/* Main split */
.main-split {{
  display: grid;
  grid-template-columns: 22% 50% 28%;
  overflow: hidden;
  position: relative;
}}

.divider {{
  position: absolute;
  top: 0; bottom: 0;
  width: 1px;
  background: var(--border-color);
  z-index: 5;
  pointer-events: none;
}}

.divider-left {{ left: 22%; }}
.divider-right {{ left: 72%; }}

/* Panels */
.panel {{
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: rgba(0,0,0,0.12) transparent;
}}
.panel::-webkit-scrollbar {{ width: 6px; }}
.panel::-webkit-scrollbar-track {{ background: transparent; }}
.panel::-webkit-scrollbar-thumb {{ background: rgba(0,0,0,0.12); border-radius: 3px; }}

/* Comments panel (left) */
.comments-panel {{
  background: #fafbfc;
  padding: 12px;
  border-right: 1px solid var(--border-color);
}}

.comment-card {{
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.15s;
  font-size: 12px;
}}

.comment-card:hover {{
  border-color: #ffc107;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}}

.comment-card.has-anchor {{
  border-left: 3px solid #ffc107;
}}

.comment-card.resolved {{
  opacity: 0.5;
  border-left-color: #ccc;
}}

.resolved-badge {{
  font-size: 9px;
  background: #e0e0e0;
  color: #888;
  padding: 1px 5px;
  border-radius: 3px;
  margin-left: 6px;
  font-weight: 400;
}}

.comment-card.active-source {{
  border-color: #ffc107;
  background: #fffde7;
  box-shadow: 0 0 0 2px #ffc107, 0 2px 8px rgba(255,193,7,0.15);
}}

.comment-card-author {{
  font-weight: 600;
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 3px;
}}

.comment-anchor-text {{
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
  margin-bottom: 4px;
  padding: 3px 6px;
  background: #fffde7;
  border-radius: 3px;
}}

.comment-card-content {{
  color: var(--text-primary);
  line-height: 1.45;
}}

.comment-replies {{
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--border-light);
}}

.comment-reply {{
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 3px;
  line-height: 1.4;
}}

.reply-author {{
  font-weight: 600;
  color: var(--text-primary);
}}

/* Comment highlights in document */
.comment-highlight {{
  background: #fff9c4;
  border-bottom: 2px solid #ffc107;
  padding: 0 1px;
  cursor: pointer;
  transition: background 0.15s;
}}

.comment-highlight:hover,
.comment-highlight.active {{
  background: #fff176;
}}

/* Document panel */
.doc-panel {{
  background: var(--bg-page);
  padding: 32px 28px;
}}

.doc-title {{
  font-size: 26px;
  font-weight: 700;
  margin-bottom: 24px;
  color: var(--text-primary);
  line-height: 1.3;
}}

.doc-subtitle {{
  font-size: 18px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 20px;
}}

.doc-h1 {{
  font-size: 22px;
  font-weight: 700;
  margin: 28px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border-light);
}}

.doc-h2 {{
  font-size: 18px;
  font-weight: 600;
  margin: 24px 0 10px;
}}

.doc-h3 {{
  font-size: 15px;
  font-weight: 600;
  margin: 20px 0 8px;
}}

.doc-h4, .doc-h5, .doc-h6 {{
  font-size: 14px;
  font-weight: 600;
  margin: 16px 0 6px;
}}

.doc-para {{
  margin: 6px 0;
  line-height: 1.65;
  color: var(--text-primary);
}}

.doc-list {{
  margin: 6px 0 6px 24px;
  line-height: 1.65;
}}

.doc-list li {{
  margin: 3px 0;
}}

/* Fallback placeholder for tables without row data */
.doc-table-placeholder {{
  margin: 12px 0;
  padding: 10px 14px;
  background: #f9f9f9;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}}

/* Full table rendering */
.doc-table-wrapper {{
  margin: 12px 0;
  overflow-x: auto;
  border-radius: 6px;
  border: 1px solid var(--border-color);
}}

.doc-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  line-height: 1.45;
}}

.doc-table th {{
  background: #f5f4f0;
  font-weight: 600;
  text-align: left;
  padding: 6px 10px;
  border-bottom: 2px solid var(--border-color);
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
}}

.doc-table td {{
  padding: 5px 10px;
  border-bottom: 1px solid var(--border-light);
  vertical-align: top;
  color: var(--text-primary);
}}

.doc-table tr:last-child td {{
  border-bottom: none;
}}

.doc-table tr:hover td {{
  background: #fafafa;
}}

.doc-table .cell-has-diff {{
  background: #fffde7;
}}

.doc-table .insert-row td {{
  background: var(--diff-ins-bg);
  border-top: 1px dashed var(--approve-green-border);
}}

.insert-marker {{
  margin: 4px 0;
  padding: 8px 12px;
  background: var(--diff-ins-bg);
  border: 1px dashed var(--approve-green-border);
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.5;
}}

.insert-snippet {{
  background: var(--diff-ins-bg);
  border-color: var(--approve-green-border);
}}

.insert-label {{
  display: inline-block;
  font-family: monospace;
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--approve-green);
  color: #fff;
  vertical-align: middle;
  margin-right: 4px;
  letter-spacing: 0.5px;
}}

/* Diff styling */
.diff-anchor {{
  border-radius: 3px;
  padding: 2px 0;
  transition: background 0.15s;
}}

.diff-anchor.highlight {{
  background: var(--focus-blue-bg);
  outline: 2px solid var(--focus-blue);
  outline-offset: 2px;
}}

.diff-del {{
  background: var(--diff-del-bg);
  color: var(--diff-del-text);
  text-decoration: line-through;
  padding: 1px 2px;
  border-radius: 2px;
}}

.diff-ins {{
  background: var(--diff-ins-bg);
  color: var(--diff-ins-text);
  text-decoration: none;
  padding: 1px 2px;
  border-radius: 2px;
  font-weight: 500;
}}

/* Margin cards panel */
.cards-panel {{
  background: var(--bg-margin);
  padding: 16px;
  border-left: 1px solid var(--border-color);
}}

.panel-label {{
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1.2px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 12px;
  padding-left: 4px;
}}

.proposal-card {{
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 10px;
  transition: all 0.15s;
  cursor: pointer;
  position: relative;
}}

.proposal-card:hover {{
  border-color: #ccc;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}}

.proposal-card.selected {{
  border-color: var(--focus-blue);
  box-shadow: 0 0 0 1px var(--focus-blue), 0 2px 12px rgba(25,118,210,0.1);
}}

.proposal-card.status-approved {{
  border-left: 3px solid var(--approve-green);
}}

.proposal-card.status-rejected {{
  border-left: 3px solid var(--reject-red);
  opacity: 0.6;
}}

.card-header {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}}

.card-id {{
  font-family: monospace;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--focus-blue-bg);
  color: var(--focus-blue);
}}

.card-type-badge {{
  font-family: monospace;
  font-size: 9px;
  font-weight: 700;
  padding: 2px 5px;
  border-radius: 3px;
  letter-spacing: 0.5px;
}}

.card-type-badge.insert {{
  background: var(--approve-green-bg);
  color: var(--approve-green);
}}

.card-type-badge.stale {{
  background: #fff3e0;
  color: #e65100;
}}

.card-title {{
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
}}

.card-status-badge {{
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}

.card-status-badge.status-pending {{
  background: var(--pending-bg);
  color: var(--pending-color);
}}

.card-status-badge.status-approved {{
  background: var(--approve-green-bg);
  color: var(--approve-green);
}}

.card-status-badge.status-rejected {{
  background: var(--reject-red-bg);
  color: var(--reject-red);
}}

.card-section {{
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 6px;
  font-style: italic;
}}

.card-warning {{
  font-size: 11px;
  color: #e65100;
  background: #fff3e0;
  padding: 4px 8px;
  border-radius: 4px;
  margin-bottom: 6px;
}}

.card-rationale {{
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
  margin-bottom: 8px;
  padding: 6px 8px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid var(--border-light);
}}

/* Source comments */
.source-comments {{
  margin-bottom: 8px;
}}

.source-comments summary {{
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px 0;
}}

.source-comment {{
  margin: 6px 0;
  padding: 6px 8px;
  background: #f5f5f5;
  border-radius: 4px;
  border-left: 2px solid var(--border-color);
}}

.comment-author {{
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 2px;
}}

.comment-quoted {{
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
  margin-bottom: 3px;
}}

.comment-content {{
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.4;
}}

.comment-missing {{
  color: #e65100;
  font-style: italic;
}}

/* Card actions */
.card-actions {{
  display: flex;
  gap: 6px;
  margin-top: 8px;
}}

.btn-approve, .btn-reject, .btn-edit, .btn-save-edit {{
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  padding: 5px 12px;
  border-radius: 4px;
  border: 1px solid;
  cursor: pointer;
  transition: all 0.15s;
}}

.btn-approve {{
  color: var(--approve-green);
  border-color: var(--approve-green-border);
  background: var(--approve-green-bg);
}}
.btn-approve:hover {{
  background: var(--approve-green);
  color: #fff;
}}

.btn-reject {{
  color: var(--reject-red);
  border-color: var(--reject-red-border);
  background: var(--reject-red-bg);
}}
.btn-reject:hover {{
  background: var(--reject-red);
  color: #fff;
}}

.btn-edit {{
  color: var(--text-secondary);
  border-color: var(--border-color);
  background: #fff;
}}
.btn-edit:hover {{
  background: #f5f5f5;
}}

/* Anchor position indicator on cards */
.anchor-indicator {{
  font-size: 10px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 3px;
  cursor: pointer;
  margin-bottom: 6px;
  display: inline-block;
  transition: all 0.15s;
}}

.anchor-indicator:hover {{
  filter: brightness(0.9);
}}

.anchor-indicator.visible {{
  background: #e8f5e9;
  color: var(--approve-green);
}}

.anchor-indicator.above {{
  background: var(--focus-blue-bg);
  color: var(--focus-blue);
}}

.anchor-indicator.below {{
  background: var(--focus-blue-bg);
  color: var(--focus-blue);
}}

.anchor-indicator.no-anchor {{
  background: #fff3e0;
  color: #e65100;
}}

.card-edit-area {{
  margin-top: 8px;
}}

.card-edit-area textarea {{
  width: 100%;
  font-family: inherit;
  font-size: 12px;
  padding: 8px;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  resize: vertical;
  margin-bottom: 6px;
}}

.btn-save-edit {{
  color: var(--focus-blue);
  border-color: var(--focus-blue);
  background: var(--focus-blue-bg);
}}

/* SVG connectors */
.connector-svg {{
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 4;
}}

.connector-line {{
  fill: none;
  stroke: var(--connector-color);
  stroke-width: 1.5;
  stroke-dasharray: 4,3;
  transition: stroke 0.15s;
}}

.connector-line.highlight {{
  stroke: var(--focus-blue);
  stroke-width: 2;
  stroke-dasharray: none;
}}

.connector-line.comment-connector {{
  stroke: #ffc107;
  stroke-width: 1;
  stroke-dasharray: 3,3;
  opacity: 0.4;
}}

.connector-line.comment-connector.highlight {{
  stroke: #f9a825;
  stroke-width: 2;
  stroke-dasharray: none;
  opacity: 1;
}}

/* Footer */
.footer {{
  padding: 10px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border-color);
  background: var(--bg-header);
  font-size: 12px;
  color: var(--text-secondary);
  z-index: 10;
}}

.footer-stats {{
  display: flex;
  gap: 16px;
}}

.stat {{
  display: flex;
  align-items: center;
  gap: 4px;
}}

.stat-dot {{
  width: 8px;
  height: 8px;
  border-radius: 50%;
}}

.stat-dot.approved {{ background: var(--approve-green); }}
.stat-dot.rejected {{ background: var(--reject-red); }}
.stat-dot.pending {{ background: var(--pending-color); }}

.btn-save {{
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 16px;
  border-radius: 5px;
  border: none;
  background: var(--focus-blue);
  color: #fff;
  cursor: pointer;
  transition: all 0.15s;
}}
.btn-save:hover {{
  filter: brightness(1.1);
}}
</style>
</head>
<body>
<div class="app">
  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <div class="header-icon">R</div>
      <div>
        <div class="header-title">{html.escape(doc_title)}</div>
        <div class="header-meta">{proposal_count} proposals to review</div>
      </div>
    </div>
    <div class="header-controls">
      <div class="shortcuts-hint">
        <kbd class="kbd">J</kbd>/<kbd class="kbd">K</kbd> navigate
        <kbd class="kbd">A</kbd> approve
        <kbd class="kbd">R</kbd> reject
        <kbd class="kbd">E</kbd> edit
        <kbd class="kbd">S</kbd> save
      </div>
    </div>
  </div>

  <!-- Main -->
  <div class="main-split">
    <div class="panel comments-panel" id="commentsPanel">
      <div class="panel-label">Comments ({comment_count})</div>
      {comments_panel_html}
    </div>
    <div class="divider divider-left"></div>
    <div class="panel doc-panel" id="docPanel">
      {document_html}
    </div>
    <div class="divider divider-right"></div>
    <svg class="connector-svg" id="connectorSvg"></svg>
    <div class="panel cards-panel" id="cardsPanel">
      <div class="panel-label">Proposals ({proposal_count})</div>
      {cards_html}
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <div class="footer-stats">
      <span class="stat"><span class="stat-dot approved"></span> <span id="statApproved">0 approved</span></span>
      <span class="stat"><span class="stat-dot rejected"></span> <span id="statRejected">0 rejected</span></span>
      <span class="stat"><span class="stat-dot pending"></span> <span id="statPending">{proposal_count} pending</span></span>
    </div>
    <button class="btn-save" onclick="saveDecisions()">Save decisions (JSON)</button>
  </div>
</div>

<script>
// Proposals data
const proposals = {proposals_json};
const savedDecisions = {saved_decisions_json};

// State
const proposalStates = {{}};
let selectedProposalId = null;

// Init
function init() {{
  // Initialize states, restoring saved decisions
  proposals.forEach(p => {{
    const saved = savedDecisions[p.id];
    proposalStates[p.id] = {{
      status: saved ? saved.status : 'pending',
      editedText: saved ? (saved.editedText || saved.proposed_text || p.proposed_text || '') : (p.proposed_text || '')
    }};

    // Apply saved status to card styling
    if (saved && saved.status !== 'pending') {{
      const card = document.getElementById('card-' + p.id);
      if (card) {{
        if (saved.status === 'approved') card.classList.add('status-approved');
        if (saved.status === 'rejected') card.classList.add('status-rejected');
      }}
      const badge = document.getElementById('status-' + p.id);
      if (badge) {{
        badge.textContent = saved.status;
        badge.className = 'card-status-badge status-' + saved.status;
      }}
      // Update edit textarea with saved text
      const ta = document.getElementById('edit-text-' + p.id);
      if (ta && saved.proposed_text) ta.value = saved.proposed_text;
    }}
  }});
  updateStats();
  updateConnectors();
  updateAnchorIndicators();

  // Click handlers on proposal cards (right panel)
  document.querySelectorAll('.proposal-card').forEach(card => {{
    card.addEventListener('click', (e) => {{
      if (e.target.tagName === 'BUTTON' || e.target.tagName === 'TEXTAREA') return;
      selectProposal(card.dataset.proposalId);
    }});
  }});

  // Click handlers on diff anchors in document (center)
  document.querySelectorAll('.diff-anchor').forEach(anchor => {{
    anchor.addEventListener('click', () => {{
      const pid = anchor.dataset.proposalId;
      if (pid) selectProposal(pid);
    }});
  }});

  // Click handlers on comment highlights in document (center)
  document.querySelectorAll('.comment-highlight').forEach(highlight => {{
    highlight.addEventListener('click', () => {{
      const cid = highlight.dataset.commentId;
      if (!cid) return;
      // Find proposal that references this comment
      const linkedProposal = proposals.find(p =>
        p.source_comment_ids && p.source_comment_ids.includes(cid));
      if (linkedProposal) {{
        selectProposal(linkedProposal.id);
      }} else {{
        scrollToCommentAnchor(cid);
      }}
    }});
  }});

  // Scroll sync for connectors and anchor indicators
  const docPanel = document.getElementById('docPanel');
  const cardsPanel = document.getElementById('cardsPanel');
  const commentsPanel = document.getElementById('commentsPanel');
  const syncUpdate = () => {{ updateConnectors(); updateAnchorIndicators(); }};
  docPanel.addEventListener('scroll', syncUpdate);
  cardsPanel.addEventListener('scroll', syncUpdate);
  commentsPanel.addEventListener('scroll', syncUpdate);
  window.addEventListener('resize', syncUpdate);

  // Select first proposal
  if (proposals.length > 0) {{
    selectProposal(proposals[0].id);
  }}
}}

function selectProposal(pid) {{
  // Deselect previous
  if (selectedProposalId) {{
    const prevCard = document.getElementById('card-' + selectedProposalId);
    if (prevCard) prevCard.classList.remove('selected');
    const prevAnchor = document.getElementById('anchor-' + selectedProposalId);
    if (prevAnchor) prevAnchor.classList.remove('highlight');
  }}

  // Clear all comment highlights
  document.querySelectorAll('.comment-card.active-source').forEach(
    c => c.classList.remove('active-source'));
  document.querySelectorAll('.comment-highlight.active').forEach(
    c => c.classList.remove('active'));

  selectedProposalId = pid;

  // Select proposal card (right)
  const card = document.getElementById('card-' + pid);
  if (card) {{
    card.classList.add('selected');
    card.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
  }}

  // Highlight diff anchor in document (center)
  const anchor = document.getElementById('anchor-' + pid);
  if (anchor) {{
    anchor.classList.add('highlight');
    anchor.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}

  // Highlight source comment cards (left) and their document anchors
  const proposal = proposals.find(p => p.id === pid);
  if (proposal && proposal.source_comment_ids) {{
    proposal.source_comment_ids.forEach(cid => {{
      // Highlight comment card
      const ccard = document.getElementById('ccard-' + cid);
      if (ccard) {{
        ccard.classList.add('active-source');
        ccard.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
      }}
      // Highlight comment anchor in document
      const canchor = document.getElementById('canchor-' + cid);
      if (canchor) canchor.classList.add('active');
    }});
  }}

  updateConnectors();
  // Re-draw after smooth scroll completes to update positions
  setTimeout(updateConnectors, 400);
  setTimeout(updateConnectors, 800);
}}

function setStatus(pid, status) {{
  const state = proposalStates[pid];
  // Toggle: if already this status, revert to pending
  if (state.status === status) {{
    state.status = 'pending';
  }} else {{
    state.status = status;
  }}

  // Update card styling
  const card = document.getElementById('card-' + pid);
  if (card) {{
    card.classList.remove('status-approved', 'status-rejected');
    if (state.status === 'approved') card.classList.add('status-approved');
    if (state.status === 'rejected') card.classList.add('status-rejected');
  }}

  // Update badge
  const badge = document.getElementById('status-' + pid);
  if (badge) {{
    badge.textContent = state.status;
    badge.className = 'card-status-badge status-' + state.status;
  }}

  updateStats();

  // Auto-advance to next pending
  if (state.status !== 'pending') {{
    const idx = proposals.findIndex(p => p.id === pid);
    for (let i = idx + 1; i < proposals.length; i++) {{
      if (proposalStates[proposals[i].id].status === 'pending') {{
        selectProposal(proposals[i].id);
        return;
      }}
    }}
  }}
}}

function toggleEdit(pid) {{
  const area = document.getElementById('edit-' + pid);
  if (area) {{
    area.style.display = area.style.display === 'none' ? 'block' : 'none';
    if (area.style.display === 'block') {{
      const ta = document.getElementById('edit-text-' + pid);
      if (ta) ta.focus();
    }}
  }}
}}

function saveEdit(pid) {{
  const ta = document.getElementById('edit-text-' + pid);
  if (ta) {{
    proposalStates[pid].editedText = ta.value;
  }}
  toggleEdit(pid);
}}

function updateStats() {{
  let approved = 0, rejected = 0, pending = 0;
  Object.values(proposalStates).forEach(s => {{
    if (s.status === 'approved') approved++;
    else if (s.status === 'rejected') rejected++;
    else pending++;
  }});
  document.getElementById('statApproved').textContent = approved + ' approved';
  document.getElementById('statRejected').textContent = rejected + ' rejected';
  document.getElementById('statPending').textContent = pending + ' pending';
}}

// Connectors - draw for ALL proposals, highlight selected
function updateConnectors() {{
  const svg = document.getElementById('connectorSvg');
  const mainSplit = document.querySelector('.main-split');
  if (!svg || !mainSplit) return;

  const rect = mainSplit.getBoundingClientRect();
  svg.setAttribute('width', rect.width);
  svg.setAttribute('height', rect.height);
  svg.style.width = rect.width + 'px';
  svg.style.height = rect.height + 'px';

  let paths = '';

  proposals.forEach(p => {{
    const anchor = document.getElementById('anchor-' + p.id);
    const card = document.getElementById('card-' + p.id);
    if (!anchor || !card) return;

    const aRect = anchor.getBoundingClientRect();
    const cRect = card.getBoundingClientRect();

    const ax = aRect.right - rect.left;
    let ay = aRect.top + aRect.height / 2 - rect.top;
    const cx = cRect.left - rect.left;
    const cy = cRect.top + 20 - rect.top;

    // Skip if card is not visible in cards panel
    if (cy < -100 || cy > rect.height + 100) return;

    // Clamp anchor Y to viewport edges if off-screen
    const anchorVisible = ay >= 0 && ay <= rect.height;
    if (ay < 0) ay = 8;
    if (ay > rect.height) ay = rect.height - 8;

    const midX = (ax + cx) / 2;
    const isSelected = selectedProposalId === p.id;
    const cls = isSelected ? 'connector-line highlight' : 'connector-line';
    paths += `<path class="${{cls}}" d="M${{ax}},${{ay}} C${{midX}},${{ay}} ${{midX}},${{cy}} ${{cx}},${{cy}}"/>`;

    // Draw comment connector lines for this proposal's source comments
    if (p.source_comment_ids && anchor) {{
      p.source_comment_ids.forEach(cid => {{
        const ccard = document.getElementById('ccard-' + cid);
        if (!ccard) return;

        const ccRect = ccard.getBoundingClientRect();
        const ccx = ccRect.right - rect.left;
        let ccy = ccRect.top + 15 - rect.top;

        // Clamp comment card Y (may be scrolled out in comment panel)
        if (ccy < 0) ccy = 8;
        if (ccy > rect.height) ccy = rect.height - 8;

        const isActive = isSelected;
        const ccls = isActive
          ? 'connector-line comment-connector highlight'
          : 'connector-line comment-connector';
        const cmidX = (ccx + ax) / 2;
        paths += `<path class="${{ccls}}" d="M${{ccx}},${{ccy}} C${{cmidX}},${{ccy}} ${{cmidX}},${{ay}} ${{ax}},${{ay}}"/>`;
      }});
    }}
  }});

  // Always draw comment connectors for the SELECTED proposal
  // (separate from the loop above which skips non-visible proposal cards)
  if (selectedProposalId) {{
    const selP = proposals.find(p => p.id === selectedProposalId);
    const selAnchor = document.getElementById('anchor-' + selectedProposalId);
    if (selP && selP.source_comment_ids && selAnchor) {{
      const saRect = selAnchor.getBoundingClientRect();
      const sax = saRect.left - rect.left;  // Left edge for comment lines
      let say = saRect.top + saRect.height / 2 - rect.top;
      if (say < 0) say = 8;
      if (say > rect.height) say = rect.height - 8;

      selP.source_comment_ids.forEach(cid => {{
        const ccard = document.getElementById('ccard-' + cid);
        if (!ccard) return;

        const ccRect = ccard.getBoundingClientRect();
        const ccx = ccRect.right - rect.left;
        let ccy = ccRect.top + 15 - rect.top;
        if (ccy < 0) ccy = 8;
        if (ccy > rect.height) ccy = rect.height - 8;

        const cmidX = (ccx + sax) / 2;
        paths += `<path class="connector-line comment-connector highlight" d="M${{ccx}},${{ccy}} C${{cmidX}},${{ccy}} ${{cmidX}},${{say}} ${{sax}},${{say}}"/>`;
      }});
    }}
  }}

  svg.innerHTML = paths;
}}

// Update anchor position indicators on all cards
function updateAnchorIndicators() {{
  const docPanel = document.getElementById('docPanel');
  const panelRect = docPanel.getBoundingClientRect();

  proposals.forEach(p => {{
    const card = document.getElementById('card-' + p.id);
    const anchor = document.getElementById('anchor-' + p.id);
    if (!card) return;

    let indicator = card.querySelector('.anchor-indicator');
    if (!indicator) {{
      indicator = document.createElement('div');
      indicator.className = 'anchor-indicator';
      indicator.onclick = (e) => {{
        e.stopPropagation();
        scrollToAnchor(p.id);
      }};
      card.insertBefore(indicator, card.firstChild);
    }}

    if (!anchor) {{
      indicator.textContent = 'No anchor';
      indicator.className = 'anchor-indicator no-anchor';
      return;
    }}

    const aRect = anchor.getBoundingClientRect();
    if (aRect.top < panelRect.top) {{
      indicator.textContent = '\u2191 Anchor above';
      indicator.className = 'anchor-indicator above';
    }} else if (aRect.bottom > panelRect.bottom) {{
      indicator.textContent = '\u2193 Anchor below';
      indicator.className = 'anchor-indicator below';
    }} else {{
      indicator.textContent = '\u2192 Anchor visible';
      indicator.className = 'anchor-indicator visible';
    }}
  }});
}}

function scrollToAnchor(pid) {{
  const anchor = document.getElementById('anchor-' + pid);
  if (anchor) {{
    anchor.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    anchor.classList.add('highlight');
    setTimeout(() => anchor.classList.remove('highlight'), 2000);
  }}
}}

function scrollToCommentAnchor(cid) {{
  const anchor = document.getElementById('canchor-' + cid);
  if (anchor) {{
    anchor.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    anchor.classList.add('active');
    setTimeout(() => anchor.classList.remove('active'), 2000);
  }}
  // Highlight the comment card
  document.querySelectorAll('.comment-card').forEach(c => c.style.outline = '');
  const card = document.getElementById('ccard-' + cid);
  if (card) {{
    card.style.outline = '2px solid #ffc107';
    setTimeout(() => card.style.outline = '', 2000);
  }}
}}

// Keyboard
document.addEventListener('keydown', (e) => {{
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;

  const idx = proposals.findIndex(p => p.id === selectedProposalId);

  switch(e.key.toLowerCase()) {{
    case 'j':
    case 'arrowdown':
      e.preventDefault();
      if (idx < proposals.length - 1) selectProposal(proposals[idx + 1].id);
      break;
    case 'k':
    case 'arrowup':
      e.preventDefault();
      if (idx > 0) selectProposal(proposals[idx - 1].id);
      break;
    case 'a':
      if (selectedProposalId) setStatus(selectedProposalId, 'approved');
      break;
    case 'r':
      if (selectedProposalId) setStatus(selectedProposalId, 'rejected');
      break;
    case 'e':
      if (selectedProposalId) toggleEdit(selectedProposalId);
      break;
    case 's':
      saveDecisions();
      break;
  }}
}});

// Save
function saveDecisions() {{
  const decisions = proposals.map(p => ({{
    id: p.id,
    title: p.title,
    status: proposalStates[p.id].status,
    current_text: p.current_text,
    proposed_text: proposalStates[p.id].editedText || p.proposed_text,
    source_comment_ids: p.source_comment_ids || [],
    target_section: p.target_section || '',
  }}));

  const output = {{
    doc_id: proposals[0]?.doc_id || '',
    reviewed_at: new Date().toISOString(),
    total_proposals: proposals.length,
    approved: decisions.filter(d => d.status === 'approved').length,
    rejected: decisions.filter(d => d.status === 'rejected').length,
    pending: decisions.filter(d => d.status === 'pending').length,
    decisions: decisions
  }};

  const blob = new Blob([JSON.stringify(output, null, 2)], {{ type: 'application/json' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'review_decisions.json';
  a.click();
  URL.revokeObjectURL(url);
}}

// Boot
init();
</script>
</body>
</html>'''


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Generate edit review HTML for Google Doc")
    parser.add_argument("--doc", required=True, help="Google Doc ID")
    parser.add_argument("--manifest", required=True, help="Path to edit manifest JSON")
    parser.add_argument("--decisions", "-d", help="Previous decisions JSON to restore statuses")
    parser.add_argument("--project", "-p", default="anaro-labs", help="Project (default: anaro-labs)")
    parser.add_argument("--output", "-o", help="Output HTML file path")
    parser.add_argument("--open", action="store_true", help="Open in browser after generating")
    args = parser.parse_args()

    # Default output filename
    if not args.output:
        doc_prefix = args.doc[:8]
        today = date.today().isoformat()
        args.output = f"review_{doc_prefix}_{today}.html"

    # Fetch document data
    doc_data = fetch_doc_data(args.doc, args.project)

    # Load manifest
    manifest = load_manifest(args.manifest)
    proposals = manifest["proposals"]
    print(f"Loaded {len(proposals)} proposals from manifest", file=sys.stderr)

    # Locate proposals in document
    enriched = locate_proposals(doc_data["content"], proposals)
    found_count = sum(1 for p in enriched if p.get("found"))
    print(f"Located {found_count}/{len(proposals)} proposals in document", file=sys.stderr)

    # Locate comments in document
    comments = doc_data["comments"]
    enriched_comments = locate_comments(
        doc_data["content"], doc_data["elements"], comments)
    comment_found = sum(1 for c in enriched_comments if c.get("found"))
    print(f"Located {comment_found}/{len(comments)} comments in document", file=sys.stderr)

    # Build HTML components
    document_html = build_document_html(
        doc_data["content"], doc_data["elements"], enriched,
        raw_body=doc_data.get("raw_body"),
        inline_objects=doc_data.get("inline_objects"),
        enriched_comments=enriched_comments,
    )
    cards_html = build_cards_html(enriched, comments)
    comments_panel_html = build_comments_panel_html(enriched_comments)

    # Add doc_id to each proposal for JS
    for p in enriched:
        p["doc_id"] = args.doc
    proposals_json = json.dumps(enriched, indent=2)

    # Load saved decisions if provided
    saved_decisions = {}
    if args.decisions:
        with open(args.decisions) as f:
            prev = json.load(f)
        for d in prev.get("decisions", []):
            saved_decisions[d["id"]] = d
        decided = sum(1 for d in saved_decisions.values() if d.get("status") != "pending")
        print(f"Restored {decided} decisions from {args.decisions}", file=sys.stderr)

    saved_decisions_json = json.dumps(saved_decisions, indent=2)

    # Generate final HTML
    final_html = generate_html(
        doc_data["title"],
        document_html,
        cards_html,
        proposals_json,
        len(proposals),
        comments_panel_html=comments_panel_html,
        comment_count=len(enriched_comments),
        saved_decisions_json=saved_decisions_json,
    )

    # Write output
    output_path = Path(args.output).resolve()
    output_path.write_text(final_html)
    print(f"Generated: {output_path}", file=sys.stderr)

    # Open in browser
    if args.open:
        webbrowser.open(f"file://{output_path}")


if __name__ == "__main__":
    main()
