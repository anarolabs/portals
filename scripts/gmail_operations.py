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

    # Reply to a message (auto-resolves thread, to, subject)
    python3 gmail_operations.py --draft --reply-to MESSAGE_ID --body "Thanks, sounds good!"

    # Reply with explicit overrides
    python3 gmail_operations.py --draft --reply-to MSG_ID --to "other@email.com" --subject "Re: Custom" --body "..."

    # Add to thread without reply headers
    python3 gmail_operations.py --draft --thread-id THREAD_ID --to "..." --subject "..." --body "..."

    # Send email
    python3 gmail_operations.py --send --to "recipient@email.com" --subject "Subject" --body "Body text"

    # Send as reply
    python3 gmail_operations.py --send --reply-to MESSAGE_ID --body "Replying inline"

    # List labels
    python3 gmail_operations.py --labels [--project estate-mate]
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
from email.mime.multipart import MIMEMultipart
from email import encoders
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from google_client import get_gmail_service, get_drive_service, get_impersonate_user


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


def create_draft(to: str, subject: str, body: str, cc: str = None,
                  thread_id: str = None, in_reply_to: str = None,
                  references: str = None, project: str = None,
                  attach: str = None):
    """Create email draft with YOUR formatting rules."""
    service = get_gmail_service(project=project)

    # Apply formatting rules
    html_body = format_body_html(body)

    # Use mixed multipart when attaching files, alternative otherwise
    if attach:
        message = MIMEMultipart("mixed")
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(html_body, "html"))
        message.attach(alt_part)
        _attach_file(message, attach)
    else:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(html_body, "html"))

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


def send_email(to: str, subject: str, body: str, cc: str = None,
               thread_id: str = None, in_reply_to: str = None,
               references: str = None, project: str = None,
               attach: str = None):
    """Send email with YOUR formatting rules."""
    service = get_gmail_service(project=project)

    # Apply formatting rules
    html_body = format_body_html(body)

    # Use mixed multipart when attaching files, alternative otherwise
    if attach:
        message = MIMEMultipart("mixed")
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(html_body, "html"))
        message.attach(alt_part)
        _attach_file(message, attach)
    else:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(html_body, "html"))

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
    group.add_argument("--send", action="store_true", help="Send email")
    group.add_argument("--labels", action="store_true", help="List labels")

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
    parser.add_argument("--attach", metavar="FILE_PATH",
                        help="Attach a file to draft or sent email")

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
                         attach=args.attach)
        else:
            send_email(to, subject, args.body, args.cc,
                       thread_id=thread_id, in_reply_to=in_reply_to,
                       references=references, project=args.project,
                       attach=args.attach)

    elif args.labels:
        list_labels(project=args.project)


if __name__ == "__main__":
    main()
