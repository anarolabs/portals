#!/usr/bin/env python3
"""
Unified multi-project Gmail operations.
Routes to the correct inbox based on --project flag or auto-detection.

Supports:
- anaro-labs: roman@anarolabs.com (default)
- estate-mate: roman@estatemate.io

FORMATTING RULES (baked in):
- Always HTML format
- <br> for line breaks (NOT <p> tags)
- <br><br> for paragraph breaks
- <ul>/<li> for bullet lists (Gmail native)
- <ol>/<li> for numbered lists (Gmail native)
- No extra padding or ugly spacing

Usage:
    # Search emails (defaults to anaro-labs)
    python3 gmail_operations.py --search "from:client@company.com" [--max 10]

    # Search in estate-mate inbox
    python3 gmail_operations.py --search "from:ari@edge-tech.at" --project estate-mate

    # Read email
    python3 gmail_operations.py --read MESSAGE_ID [--project estate-mate]

    # List attachments on a message
    python3 gmail_operations.py --attachments MESSAGE_ID [--project estate-mate]

    # Download attachment
    python3 gmail_operations.py --download-attachment MESSAGE_ID --attachment-id ATT_ID --output /tmp/file.pdf

    # Deep read (body + attachments + Drive links with metadata)
    python3 gmail_operations.py --deep-read MESSAGE_ID [--project estate-mate]

    # Create draft
    python3 gmail_operations.py --draft --to "recipient@email.com" --subject "Subject" --body "Body text"

    # Draft with inline images embedded in the body (walkthrough / screenshots).
    # Reference each image in the body with a {{inline:N}} token (1-based, in --inline order);
    # any image without a token is appended in order at the end.
    python3 gmail_operations.py --draft --to "x@y.com" --subject "So geht's" \
        --body "Schritt 1:\n{{inline:1}}\nSchritt 2:\n{{inline:2}}" \
        --inline /path/step1.png --inline /path/step2.png

    # Attach a file as a download (not inline)
    python3 gmail_operations.py --draft --to "x@y.com" --subject "S" --body "..." --attach /path/report.pdf

    # Reply to a message (auto-resolves thread, to, subject)
    python3 gmail_operations.py --draft --reply-to MESSAGE_ID --body "Thanks, sounds good!"

    # Reply with explicit overrides
    python3 gmail_operations.py --draft --reply-to MSG_ID --to "other@email.com" --subject "Re: Custom" --body "..."

    # Add to thread without reply headers
    python3 gmail_operations.py --draft --thread-id THREAD_ID --to "..." --subject "..." --body "..."

    # Signature: the sending identity's Gmail signature is appended by default.
    # Opt out with --no-signature.
    python3 gmail_operations.py --draft --to "x@y.com" --subject "S" --body "..." --no-signature

    # List drafts (returns the draft IDs that update/delete need)
    python3 gmail_operations.py --list-drafts [--max 20] [--project estate-mate]

    # Update an existing draft in place (unspecified fields carry over,
    # attachments are preserved unless --attach replaces them)
    python3 gmail_operations.py --draft-update DRAFT_ID --body "Revised text"
    python3 gmail_operations.py --draft-update DRAFT_ID --subject "New subject" --to "other@email.com"

    # Delete draft(s)
    python3 gmail_operations.py --draft-delete DRAFT_ID
    python3 gmail_operations.py --draft-delete ID1,ID2,ID3

    # Send email
    python3 gmail_operations.py --send --to "recipient@email.com" --subject "Subject" --body "Body text"

    # Send as reply
    python3 gmail_operations.py --send --reply-to MESSAGE_ID --body "Replying inline"

    # List labels
    python3 gmail_operations.py --labels [--project estate-mate]

    # Modify labels on a message
    python3 gmail_operations.py --modify-labels MESSAGE_ID --add-labels "Newsletters/Processed" --remove-labels "Newsletters/Unprocessed" --project anaro-labs
    python3 gmail_operations.py --modify-labels MESSAGE_ID --add-labels "Newsletters/Flagged" --project anaro-labs
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email import encoders
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from google_client import get_gmail_service, get_drive_service, get_impersonate_user


# Local fallback: {"roman@estatemate.io": "<div>...</div>", ...}
SIGNATURE_FALLBACK_FILE = Path(__file__).parent.parent / "config" / "signatures.json"

# Marker Gmail itself uses; keeps the block collapsible in the client and lets us
# detect a body that already carries a signature.
SIGNATURE_MARKER = 'class="gmail_signature"'

_SIGNATURE_CACHE = {}


def get_signature(project: str = None) -> str:
    """Return the sendAs signature HTML for the project's sending identity.

    Reads the live Gmail setting (the same block a hand-written message gets),
    which the existing gmail.readonly scope already covers - no settings scope
    needed. Falls back to config/signatures.json keyed by the identity address
    if the API call fails, and to "" if there is nothing to append.
    """
    identity = get_impersonate_user(project=project)
    if identity in _SIGNATURE_CACHE:
        return _SIGNATURE_CACHE[identity]

    signature = ""
    try:
        service = get_gmail_service(project=project)
        entries = service.users().settings().sendAs().list(
            userId="me"
        ).execute().get("sendAs", [])
        match = next(
            (e for e in entries if e.get("sendAsEmail", "").lower() == identity.lower()),
            None,
        ) or next((e for e in entries if e.get("isDefault")), None)
        if match:
            signature = (match.get("signature") or "").strip()
    except Exception:
        signature = ""

    if not signature and SIGNATURE_FALLBACK_FILE.exists():
        try:
            stored = json.loads(SIGNATURE_FALLBACK_FILE.read_text(encoding="utf-8"))
            signature = (stored.get(identity) or "").strip()
        except Exception:
            signature = ""

    _SIGNATURE_CACHE[identity] = signature
    return signature


def append_signature(html_body: str, project: str = None) -> str:
    """Append the sending identity's signature to an already-formatted HTML body.

    No-op when the body already carries a signature block, so re-running
    --draft-update on a draft we created does not stack copies.
    """
    if SIGNATURE_MARKER in html_body:
        return html_body

    signature = get_signature(project=project)
    if not signature:
        return html_body

    return f'{html_body}<br><br><div {SIGNATURE_MARKER} data-smartmail="gmail_signature">{signature}</div>'


def format_body_html(text: str) -> str:
    """
    Convert plain text to HTML with Gmail-compatible formatting:
    - <br> for line breaks
    - <br><br> for paragraph breaks
    - <ul>/<li> for bullet lists (Gmail native)
    - <ol>/<li> for numbered lists (Gmail native)
    - **bold** to <strong>bold</strong>
    """
    import re

    if not text:
        return ""

    # Already HTML? Return as-is
    if text.strip().startswith("<") and "<br>" in text.lower():
        return text

    # Convert **bold** to <strong>bold</strong>
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

    lines = text.split("\n")
    result_parts = []
    i = 0

    # List styles for Gmail compatibility
    ul_style = 'style="margin:0 0 10px 25px; padding:0;"'
    ol_style = 'style="margin:0 0 10px 25px; padding:0;"'

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check for bullet list (- or *)
        if stripped.startswith("- ") or stripped.startswith("* "):
            # Collect all bullet items (allowing blank lines between)
            bullet_items = []
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith("- "):
                    bullet_items.append(s[2:])
                    i += 1
                elif s.startswith("* "):
                    bullet_items.append(s[2:])
                    i += 1
                elif s == "":
                    # Empty line - peek ahead to see if list continues
                    peek = i + 1
                    while peek < len(lines) and lines[peek].strip() == "":
                        peek += 1
                    # Check if next non-empty line is a bullet item
                    next_line = lines[peek].strip() if peek < len(lines) else ""
                    if next_line.startswith("- ") or next_line.startswith("* "):
                        i += 1  # Skip this empty line, continue collecting
                    else:
                        break  # List has ended
                else:
                    break

            # Build HTML list
            list_html = f'<ul {ul_style}>'
            for item in bullet_items:
                list_html += f'<li>{item}</li>'
            list_html += '</ul>'
            result_parts.append(list_html)

        # Check for numbered list (1. 2. 3. etc)
        elif re.match(r'^\d+\.\s', stripped):
            # Collect all numbered items (allowing blank lines between)
            numbered_items = []
            while i < len(lines):
                s = lines[i].strip()
                match = re.match(r'^\d+\.\s+(.+)$', s)
                if match:
                    numbered_items.append(match.group(1))
                    i += 1
                elif s == "":
                    # Empty line - peek ahead to see if list continues
                    peek = i + 1
                    while peek < len(lines) and lines[peek].strip() == "":
                        peek += 1
                    # Check if next non-empty line is a numbered item
                    if peek < len(lines) and re.match(r'^\d+\.\s', lines[peek].strip()):
                        i += 1  # Skip this empty line, continue collecting
                    else:
                        break  # List has ended
                else:
                    break

            # Build HTML list
            list_html = f'<ol {ol_style}>'
            for item in numbered_items:
                list_html += f'<li>{item}</li>'
            list_html += '</ol>'
            result_parts.append(list_html)

        # Empty line = paragraph break
        elif stripped == "":
            result_parts.append("<br>")
            i += 1

        # Regular line
        else:
            result_parts.append(line)
            i += 1

    # Join parts with <br> between non-list items
    result = ""
    for j, part in enumerate(result_parts):
        result += part
        # Add <br> after non-list, non-br parts if not last
        if j < len(result_parts) - 1:
            if not part.startswith("<ul") and not part.startswith("<ol") and part != "<br>":
                result += "<br>"

    return result


def _fetch_reply_headers(message_id: str, project: str = None) -> dict:
    """Fetch original message metadata needed for threading a reply.

    Returns dict with: thread_id, message_id_header, subject, from_addr, references.
    Uses format="metadata" for a lightweight fetch (no body download).
    """
    service = get_gmail_service(project=project)

    msg = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["From", "Subject", "Message-ID", "References"]
    ).execute()

    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

    return {
        "thread_id": msg.get("threadId"),
        "message_id_header": headers.get("Message-ID", ""),
        "subject": headers.get("Subject", ""),
        "from_addr": headers.get("From", ""),
        "references": headers.get("References", ""),
    }


def search_emails(query: str, max_results: int = 10, project: str = None):
    """Search emails using Gmail search syntax."""
    service = get_gmail_service(project=project)

    try:
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results
        ).execute()

        messages = results.get("messages", [])

        if not messages:
            print(json.dumps({"results": [], "count": 0, "project": project or "auto"}))
            return

        # Get message details
        output = []
        for msg in messages:
            msg_data = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}

            output.append({
                "id": msg["id"],
                "thread_id": msg["threadId"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": msg_data.get("snippet", "")
            })

        print(json.dumps({"results": output, "count": len(output)}, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def _walk_mime_parts(payload):
    """Recursively walk MIME parts, yielding each leaf part."""
    if "parts" in payload:
        for part in payload["parts"]:
            yield from _walk_mime_parts(part)
    else:
        yield payload


def _extract_body_and_attachments(payload):
    """Extract text body, attachment metadata, and Drive links from a message payload."""
    body_plain = ""
    body_html = ""
    attachments = []

    for part in _walk_mime_parts(payload):
        mime_type = part.get("mimeType", "")
        filename = part.get("filename", "")
        body_data = part.get("body", {})

        # Attachment: has filename or attachmentId
        if filename or body_data.get("attachmentId"):
            if filename:  # skip inline parts without filename
                attachments.append({
                    "filename": filename,
                    "mime_type": mime_type,
                    "size": body_data.get("size", 0),
                    "attachment_id": body_data.get("attachmentId", ""),
                })
            continue

        # Body text
        data = body_data.get("data", "")
        if not data:
            continue

        decoded = base64.urlsafe_b64decode(data).decode("utf-8")
        if mime_type == "text/plain" and not body_plain:
            body_plain = decoded
        elif mime_type == "text/html" and not body_html:
            body_html = decoded

    body = body_plain or body_html

    # Extract Google Drive links from body
    drive_links = []
    if body:
        drive_pattern = r'https://(?:docs|drive|sheets|slides)\.google\.com/[^\s<>"\')\]]*'
        drive_links = list(set(re.findall(drive_pattern, body)))

    return body, attachments, drive_links


def read_email(message_id: str, project: str = None):
    """Read full email content with attachment metadata and Drive links."""
    service = get_gmail_service(project=project)

    try:
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()

        headers = {}
        for h in msg.get("payload", {}).get("headers", []):
            headers[h["name"]] = h["value"]

        payload = msg.get("payload", {})
        body, attachments, drive_links = _extract_body_and_attachments(payload)

        output = {
            "id": message_id,
            "thread_id": msg.get("threadId"),
            "message_id_header": headers.get("Message-ID", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body,
            "labels": msg.get("labelIds", []),
            "attachments": attachments,
            "drive_links": drive_links,
        }

        print(json.dumps(output, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def list_attachments(message_id: str, project: str = None):
    """List all attachments on a message (metadata only)."""
    service = get_gmail_service(project=project)

    try:
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()

        payload = msg.get("payload", {})
        _, attachments, _ = _extract_body_and_attachments(payload)

        print(json.dumps({"message_id": message_id, "attachments": attachments}, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def download_attachment(message_id: str, attachment_id: str, output_path: str, project: str = None):
    """Download an attachment and save to disk."""
    service = get_gmail_service(project=project)

    try:
        att = service.users().messages().attachments().get(
            userId="me",
            messageId=message_id,
            id=attachment_id
        ).execute()

        data = base64.urlsafe_b64decode(att["data"])

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        with open(output_path, "wb") as f:
            f.write(data)

        print(json.dumps({
            "success": True,
            "message_id": message_id,
            "attachment_id": attachment_id,
            "output_path": output_path,
            "size_bytes": len(data),
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def deep_read_email(message_id: str, project: str = None):
    """Full intelligence read: body + attachments + Drive link metadata."""
    service = get_gmail_service(project=project)

    try:
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()

        headers = {}
        for h in msg.get("payload", {}).get("headers", []):
            headers[h["name"]] = h["value"]

        payload = msg.get("payload", {})
        body, attachments, drive_links = _extract_body_and_attachments(payload)

        # Enrich Drive links with metadata
        drive_metadata = []
        if drive_links:
            try:
                drive_service = get_drive_service(project=project)
                for link in drive_links:
                    file_id = _extract_drive_file_id(link)
                    if not file_id:
                        drive_metadata.append({"url": link, "error": "could not extract file ID"})
                        continue
                    try:
                        file_meta = drive_service.files().get(
                            fileId=file_id,
                            fields="id,name,mimeType,modifiedTime,size"
                        ).execute()
                        drive_metadata.append({
                            "url": link,
                            "file_id": file_meta.get("id"),
                            "name": file_meta.get("name"),
                            "mime_type": file_meta.get("mimeType"),
                            "modified_time": file_meta.get("modifiedTime"),
                            "size": file_meta.get("size"),
                        })
                    except Exception as drive_err:
                        drive_metadata.append({"url": link, "error": str(drive_err)})
            except Exception:
                drive_metadata = [{"url": link, "error": "Drive service unavailable"} for link in drive_links]

        output = {
            "id": message_id,
            "thread_id": msg.get("threadId"),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body,
            "labels": msg.get("labelIds", []),
            "attachments": attachments,
            "drive_links": drive_metadata,
        }

        print(json.dumps(output, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def _extract_drive_file_id(url: str) -> "str | None":
    """Extract Google Drive file ID from various URL formats."""
    patterns = [
        r'/d/([a-zA-Z0-9_-]+)',           # /d/FILE_ID/
        r'id=([a-zA-Z0-9_-]+)',            # ?id=FILE_ID
        r'/folders/([a-zA-Z0-9_-]+)',      # /folders/FOLDER_ID
        r'/spreadsheets/d/([a-zA-Z0-9_-]+)',  # sheets
        r'/document/d/([a-zA-Z0-9_-]+)',   # docs
        r'/presentation/d/([a-zA-Z0-9_-]+)',  # slides
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _attach_file(message: MIMEMultipart, file_path: str):
    """Attach a file to a MIMEMultipart message."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Attachment not found: {file_path}")

    content_type, _ = mimetypes.guess_type(str(path))
    if content_type is None:
        content_type = "application/octet-stream"
    main_type, sub_type = content_type.split("/", 1)

    with open(path, "rb") as f:
        attachment = MIMEBase(main_type, sub_type)
        attachment.set_payload(f.read())
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename=path.name)
    message.attach(attachment)


def _make_inline_image(file_path: str, cid: str):
    """Build a MIME part for an image referenced inline in the HTML body via cid.

    Marked `Content-Disposition: inline` with a `Content-ID`, so `<img src="cid:...">`
    renders in-body rather than as a download.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Inline image not found: {file_path}")

    content_type, _ = mimetypes.guess_type(str(path))
    if content_type is None:
        content_type = "application/octet-stream"
    main_type, sub_type = content_type.split("/", 1)

    data = path.read_bytes()
    if main_type == "image":
        part = MIMEImage(data, _subtype=sub_type)
    else:
        # Non-image inline is unusual but supported (e.g. inline SVG served as text).
        part = MIMEBase(main_type, sub_type)
        part.set_payload(data)
        encoders.encode_base64(part)
    part.add_header("Content-ID", f"<{cid}>")
    part.add_header("Content-Disposition", "inline", filename=path.name)
    return part


def _inline_img_tag(cid: str) -> str:
    """Neutral, mobile-safe <img> tag for an inline image. Scales to container width."""
    return (f'<img src="cid:{cid}" '
            f'style="display:block;max-width:100%;height:auto;margin:12px 0;" alt="">')


def _embed_inline_images(html_body: str, inline_files: list):
    """Wire inline image files into the HTML body.

    Each file gets a stable cid (`inline1`, `inline2`, ...). Anywhere the body
    contains a token `{{inline:REF}}` - where REF is the 1-based index, the file
    basename, or the basename without extension - the token is replaced with an
    <img> tag. Any provided image not referenced by a token is appended, in order,
    after the body. Returns (updated_html, [(cid, file_path), ...]) where the list
    carries every provided image so the caller attaches them all.
    """
    parts = []           # (cid, file_path) for every provided image, in order
    by_index = {}
    by_name = {}
    for i, fp in enumerate(inline_files, start=1):
        cid = f"inline{i}"
        parts.append((cid, fp))
        by_index[str(i)] = cid
        by_name[Path(fp).name] = cid
        by_name[Path(fp).stem] = cid

    used = set()

    def _repl(m):
        ref = m.group(1).strip()
        cid = by_index.get(ref) or by_name.get(ref)
        if not cid:
            return m.group(0)  # leave unknown tokens untouched
        used.add(cid)
        return _inline_img_tag(cid)

    html_body = re.sub(r"\{\{\s*inline:\s*([^}]+?)\s*\}\}", _repl, html_body)

    leftovers = [(cid, fp) for cid, fp in parts if cid not in used]
    if leftovers:
        html_body += "".join(_inline_img_tag(cid) for cid, _ in leftovers)

    return html_body, parts


def _compose_message(html_body: str, attach: list = None, inline: list = None):
    """Assemble the MIME tree for a message body plus optional inline images and
    file attachments. Nesting: mixed( related( alternative(html), inline-imgs ), files ).
    Any level with nothing to add collapses away, preserving prior behaviour when
    neither inline nor attach is passed (a bare multipart/alternative)."""
    attach = attach or []
    inline = inline or []  # list of (cid, file_path)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html"))

    if inline:
        related = MIMEMultipart("related")
        related.attach(alt)
        for cid, fp in inline:
            related.attach(_make_inline_image(fp, cid))
        content = related
    else:
        content = alt

    if attach:
        mixed = MIMEMultipart("mixed")
        mixed.attach(content)
        for file_path in attach:
            _attach_file(mixed, file_path)
        return mixed

    return content


def create_draft(to: str, subject: str, body: str, cc: str = None,
                  thread_id: str = None, in_reply_to: str = None,
                  references: str = None, project: str = None,
                  attach: list = None, inline: list = None,
                  signature: bool = True):
    """Create email draft with YOUR formatting rules."""
    service = get_gmail_service(project=project)

    # Apply formatting rules
    html_body = format_body_html(body)
    if signature:
        html_body = append_signature(html_body, project=project)

    # Wire inline images (cid) into the body, then build the MIME tree.
    inline_parts = []
    if inline:
        html_body, inline_parts = _embed_inline_images(html_body, inline)
    message = _compose_message(html_body, attach=attach, inline=inline_parts)

    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references

    # Encode
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:
        api_body = {"message": {"raw": raw}}
        if thread_id:
            api_body["message"]["threadId"] = thread_id

        draft = service.users().drafts().create(
            userId="me",
            body=api_body
        ).execute()

        result = {
            "success": True,
            "draft_id": draft.get("id"),
            "message_id": draft.get("message", {}).get("id"),
            "inbox": get_impersonate_user(project=project),
        }
        if thread_id:
            result["thread_id"] = thread_id

        print(json.dumps(result, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def list_drafts(max_results: int = 10, project: str = None):
    """List drafts with their draft IDs (the handle --draft-update/--draft-delete need)."""
    service = get_gmail_service(project=project)

    try:
        resp = service.users().drafts().list(
            userId="me", maxResults=max_results
        ).execute()
        drafts = resp.get("drafts", [])

        output = []
        for d in drafts:
            detail = service.users().drafts().get(
                userId="me", id=d["id"], format="metadata"
            ).execute()
            msg = detail.get("message", {})
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            output.append({
                "draft_id": d["id"],
                "message_id": msg.get("id"),
                "thread_id": msg.get("threadId"),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })

        print(json.dumps({
            "drafts": output,
            "count": len(output),
            "inbox": get_impersonate_user(project=project),
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def _reattach_bytes(message: MIMEMultipart, filename: str, data: bytes):
    """Attach raw bytes to a MIMEMultipart message under the original filename."""
    content_type, _ = mimetypes.guess_type(filename)
    if content_type is None:
        content_type = "application/octet-stream"
    main_type, sub_type = content_type.split("/", 1)

    attachment = MIMEBase(main_type, sub_type)
    attachment.set_payload(data)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    message.attach(attachment)


def update_draft(draft_id: str, to: str = None, cc: str = None, subject: str = None,
                 body: str = None, attach: list = None, project: str = None,
                 signature: bool = True):
    """Update an existing draft in place (same draft ID, drafts only - never sends).

    Fields not passed carry over from the current draft: recipients, subject,
    body, threading headers (In-Reply-To/References/threadId), and attachments.
    Passing --attach replaces the attachment set; otherwise existing attachments
    are re-fetched and preserved. A passed --body goes through the same HTML
    formatting rules as --draft; a carried-over body is reused verbatim.
    """
    service = get_gmail_service(project=project)

    if not any([to, cc, subject, body, attach]):
        print(json.dumps({"error": "Nothing to update: pass at least one of --to, --cc, --subject, --body, --attach"}), file=sys.stderr)
        sys.exit(1)

    try:
        existing = service.users().drafts().get(
            userId="me", id=draft_id, format="full"
        ).execute()
    except Exception as e:
        print(json.dumps({"error": f"Draft not found: {e}", "draft_id": draft_id}), file=sys.stderr)
        sys.exit(1)

    try:
        msg = existing.get("message", {})
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        existing_body, existing_atts, _ = _extract_body_and_attachments(payload)

        final_to = to or headers.get("To", "")
        final_cc = cc or headers.get("Cc")
        final_subject = subject or headers.get("Subject", "")
        thread_id = msg.get("threadId")
        in_reply_to = headers.get("In-Reply-To")
        references = headers.get("References")

        if not final_to or not final_subject:
            print(json.dumps({"error": "Draft is missing To/Subject and none passed; provide --to/--subject"}), file=sys.stderr)
            sys.exit(1)

        # New body goes through formatting rules; carried-over body is reused raw
        # (it is already the HTML we generated at create time - re-formatting
        # would double-convert it).
        html_body = format_body_html(body) if body else existing_body
        if body and signature:
            html_body = append_signature(html_body, project=project)

        # Attachment plan: --attach replaces; otherwise carry originals over
        carried_attachments = []
        message = None
        if attach:
            message = MIMEMultipart("mixed")
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(MIMEText(html_body, "html"))
            message.attach(alt_part)
            for file_path in attach:
                _attach_file(message, file_path)
        elif existing_atts:
            message = MIMEMultipart("mixed")
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(MIMEText(html_body, "html"))
            message.attach(alt_part)
            for att in existing_atts:
                att_data = service.users().messages().attachments().get(
                    userId="me", messageId=msg["id"], id=att["attachment_id"]
                ).execute()
                data = base64.urlsafe_b64decode(att_data["data"])
                _reattach_bytes(message, att["filename"], data)
                carried_attachments.append(att["filename"])
        else:
            message = MIMEMultipart("alternative")
            message.attach(MIMEText(html_body, "html"))

        message["To"] = final_to
        message["Subject"] = final_subject
        if final_cc:
            message["Cc"] = final_cc
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = references

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        api_body = {"message": {"raw": raw}}
        if thread_id:
            api_body["message"]["threadId"] = thread_id

        updated = service.users().drafts().update(
            userId="me", id=draft_id, body=api_body
        ).execute()

        result = {
            "success": True,
            "draft_id": updated.get("id"),
            "message_id": updated.get("message", {}).get("id"),
            "to": final_to,
            "subject": final_subject,
            "updated_fields": [f for f, v in [("to", to), ("cc", cc), ("subject", subject), ("body", body), ("attachments", attach)] if v],
            "inbox": get_impersonate_user(project=project),
        }
        if thread_id:
            result["thread_id"] = thread_id
        if attach:
            result["attachments"] = [Path(p).name for p in attach]
            if existing_atts:
                result["replaced_attachments"] = [a["filename"] for a in existing_atts]
        elif carried_attachments:
            result["attachments_carried_over"] = carried_attachments

        print(json.dumps(result, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e), "draft_id": draft_id}), file=sys.stderr)
        sys.exit(1)


def delete_draft(draft_ids: list, project: str = None):
    """Delete one or more drafts by draft ID (drafts only - sent mail untouched)."""
    service = get_gmail_service(project=project)

    successes = []
    errors = []
    for did in draft_ids:
        try:
            service.users().drafts().delete(userId="me", id=did).execute()
            successes.append(did)
        except Exception as e:
            errors.append({"draft_id": did, "error": str(e)})

    print(json.dumps({
        "success": not errors,
        "deleted": successes,
        "deleted_count": len(successes),
        "errors": errors,
        "inbox": get_impersonate_user(project=project),
    }, indent=2))

    if errors:
        sys.exit(1)


def send_email(to: str, subject: str, body: str, cc: str = None,
               thread_id: str = None, in_reply_to: str = None,
               references: str = None, project: str = None,
               attach: list = None, inline: list = None,
               signature: bool = True):
    """Send email with YOUR formatting rules."""
    service = get_gmail_service(project=project)

    # Apply formatting rules
    html_body = format_body_html(body)
    if signature:
        html_body = append_signature(html_body, project=project)

    # Wire inline images (cid) into the body, then build the MIME tree.
    inline_parts = []
    if inline:
        html_body, inline_parts = _embed_inline_images(html_body, inline)
    message = _compose_message(html_body, attach=attach, inline=inline_parts)

    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references

    # Encode
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:
        api_body = {"raw": raw}
        if thread_id:
            api_body["threadId"] = thread_id

        sent = service.users().messages().send(
            userId="me",
            body=api_body
        ).execute()

        print(json.dumps({
            "success": True,
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "sent_from": get_impersonate_user(project=project),
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def list_labels(project: str = None):
    """List all Gmail labels."""
    service = get_gmail_service(project=project)

    try:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])

        output = [{"id": l["id"], "name": l["name"], "type": l.get("type", "user")} for l in labels]
        print(json.dumps({
            "labels": output,
            "count": len(output),
            "inbox": get_impersonate_user(project=project),
        }, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def modify_labels(message_id: str, add_labels: list = None, remove_labels: list = None, project: str = None):
    """Add and/or remove labels from a message by label name."""
    service = get_gmail_service(project=project)

    try:
        # Fetch all labels to build name -> id mapping
        all_labels = service.users().labels().list(userId="me").execute().get("labels", [])
        name_to_id = {l["name"]: l["id"] for l in all_labels}
        label_types = {l["name"]: l.get("type", "user") for l in all_labels}

        def resolve_label_id(label_name: str) -> str:
            """Resolve a label name to its ID, creating user labels if needed."""
            if label_name in name_to_id:
                return name_to_id[label_name]

            # System labels can't be created - error out
            # (system labels like INBOX, SPAM etc. always exist, so this catches typos)
            system_names = [n for n, t in label_types.items() if t == "system"]
            if label_name.upper() in [s.upper() for s in system_names]:
                raise ValueError(f"System label '{label_name}' not found (check spelling)")

            # Create user label
            created = service.users().labels().create(
                userId="me",
                body={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                }
            ).execute()
            # Cache for subsequent lookups
            name_to_id[created["name"]] = created["id"]
            label_types[created["name"]] = "user"
            return created["id"]

        add_ids = [resolve_label_id(n) for n in (add_labels or [])]
        remove_ids = [resolve_label_id(n) for n in (remove_labels or [])]

        # Build modify request body
        modify_body = {}
        if add_ids:
            modify_body["addLabelIds"] = add_ids
        if remove_ids:
            modify_body["removeLabelIds"] = remove_ids

        result = service.users().messages().modify(
            userId="me",
            id=message_id,
            body=modify_body
        ).execute()

        # Map updated label IDs back to names
        id_to_name = {v: k for k, v in name_to_id.items()}
        updated_labels = [id_to_name.get(lid, lid) for lid in result.get("labelIds", [])]

        output = {
            "success": True,
            "message_id": message_id,
            "added": add_labels or [],
            "removed": remove_labels or [],
            "current_labels": updated_labels,
            "inbox": get_impersonate_user(project=project),
        }

        print(json.dumps(output, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Unified multi-project Gmail operations")

    # Project routing
    parser.add_argument("--project", "-p",
                        help="Project to use (anaro-labs, estate-mate). Auto-detects if not specified.")

    # Operation flags
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--search", metavar="QUERY", help="Search emails (Gmail syntax)")
    group.add_argument("--read", metavar="MESSAGE_ID", help="Read email by ID")
    group.add_argument("--attachments", metavar="MESSAGE_ID", help="List attachments on a message")
    group.add_argument("--download-attachment", metavar="MESSAGE_ID", help="Download attachment from a message")
    group.add_argument("--deep-read", metavar="MESSAGE_ID", help="Deep read: body + attachments + Drive link metadata")
    group.add_argument("--draft", action="store_true", help="Create draft")
    group.add_argument("--list-drafts", action="store_true", help="List drafts with their draft IDs")
    group.add_argument("--draft-update", metavar="DRAFT_ID", help="Update an existing draft in place (fields not passed carry over)")
    group.add_argument("--draft-delete", metavar="DRAFT_ID", help="Delete draft(s) by ID (comma-separated for batch)")
    group.add_argument("--send", action="store_true", help="Send email")
    group.add_argument("--labels", action="store_true", help="List labels")
    group.add_argument("--modify-labels", metavar="MESSAGE_ID", help="Add/remove labels on a message")

    # Label modification arguments
    parser.add_argument("--add-labels", help="Comma-separated label names to add (for --modify-labels)")
    parser.add_argument("--remove-labels", help="Comma-separated label names to remove (for --modify-labels)")

    # Attachment arguments
    parser.add_argument("--attachment-id", help="Attachment ID (for --download-attachment)")
    parser.add_argument("--output", help="Output file path (for --download-attachment)")

    # Email arguments
    parser.add_argument("--to", help="Recipient email")
    parser.add_argument("--cc", help="CC recipients")
    parser.add_argument("--subject", help="Email subject")
    parser.add_argument("--body", help="Email body (plain text, will be formatted)")
    parser.add_argument("--max", type=int, default=10, help="Max search results (default: 10)")

    # Attachment
    parser.add_argument("--attach", metavar="FILE_PATH", action="append",
                        help="Attach file(s) to draft or sent email (repeatable)")

    # Inline images (embedded in the body via cid, not as downloads)
    parser.add_argument("--inline", metavar="FILE_PATH", action="append",
                        help="Embed image(s) inline in the body (repeatable, order = index). "
                             "Reference in --body with {{inline:N}}, {{inline:filename.png}}, or "
                             "{{inline:filename}}; unreferenced images are appended in order. "
                             "For --draft and --send only.")

    # Signature (on by default: drafts land send-ready)
    parser.add_argument("--no-signature", action="store_true",
                        help="Do not append the sending identity's Gmail signature "
                             "(appended by default on --draft, --draft-update with a "
                             "new --body, and --send)")

    # Thread/reply arguments
    parser.add_argument("--reply-to", metavar="MESSAGE_ID",
                        help="Reply to this message (auto-resolves thread, to, subject)")
    parser.add_argument("--thread-id", metavar="THREAD_ID",
                        help="Add to thread without reply headers (escape hatch)")

    args = parser.parse_args()

    if args.search:
        search_emails(args.search, args.max, project=args.project)

    elif args.read:
        read_email(args.read, project=args.project)

    elif args.attachments:
        list_attachments(args.attachments, project=args.project)

    elif args.download_attachment:
        if not args.attachment_id or not args.output:
            parser.error("--download-attachment requires --attachment-id and --output")
        download_attachment(args.download_attachment, args.attachment_id, args.output, project=args.project)

    elif args.deep_read:
        deep_read_email(args.deep_read, project=args.project)

    elif args.list_drafts:
        list_drafts(args.max, project=args.project)

    elif args.draft_update:
        update_draft(args.draft_update, to=args.to, cc=args.cc, subject=args.subject,
                     body=args.body, attach=args.attach, project=args.project,
                     signature=not args.no_signature)

    elif args.draft_delete:
        ids = [d.strip() for d in args.draft_delete.split(",") if d.strip()]
        delete_draft(ids, project=args.project)

    elif args.draft or args.send:
        # Resolve reply context if --reply-to provided
        thread_id = args.thread_id
        in_reply_to = None
        references = None
        to = args.to
        subject = args.subject

        if args.reply_to:
            try:
                reply_ctx = _fetch_reply_headers(args.reply_to, project=args.project)
            except Exception as e:
                print(json.dumps({"error": f"Failed to fetch reply headers: {e}"}),
                      file=sys.stderr)
                sys.exit(1)

            thread_id = reply_ctx["thread_id"]
            in_reply_to = reply_ctx["message_id_header"]

            # Build References chain: existing refs + original Message-ID
            ref_parts = []
            if reply_ctx["references"]:
                ref_parts.append(reply_ctx["references"])
            if reply_ctx["message_id_header"]:
                ref_parts.append(reply_ctx["message_id_header"])
            references = " ".join(ref_parts) if ref_parts else None

            # Auto-populate to and subject if not explicitly given
            if not to:
                to = reply_ctx["from_addr"]
            if not subject:
                orig_subject = reply_ctx["subject"]
                subject = orig_subject if orig_subject.startswith("Re:") else f"Re: {orig_subject}"

        # Validate required fields
        if not all([to, subject, args.body]):
            missing = [f for f, v in [("--to", to), ("--subject", subject), ("--body", args.body)] if not v]
            parser.error(f"Missing required fields: {', '.join(missing)}. "
                         "Use --reply-to to auto-resolve --to and --subject.")

        if args.draft:
            create_draft(to, subject, args.body, args.cc,
                         thread_id=thread_id, in_reply_to=in_reply_to,
                         references=references, project=args.project,
                         attach=args.attach, inline=args.inline,
                         signature=not args.no_signature)
        else:
            send_email(to, subject, args.body, args.cc,
                       thread_id=thread_id, in_reply_to=in_reply_to,
                       references=references, project=args.project,
                       attach=args.attach, inline=args.inline,
                       signature=not args.no_signature)

    elif args.labels:
        list_labels(project=args.project)

    elif args.modify_labels:
        if not args.add_labels and not args.remove_labels:
            parser.error("--modify-labels requires at least one of --add-labels or --remove-labels")
        add_list = [l.strip() for l in args.add_labels.split(",")] if args.add_labels else []
        remove_list = [l.strip() for l in args.remove_labels.split(",")] if args.remove_labels else []
        modify_labels(args.modify_labels, add_labels=add_list, remove_labels=remove_list, project=args.project)


if __name__ == "__main__":
    main()
