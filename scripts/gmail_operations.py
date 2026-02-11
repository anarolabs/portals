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
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from google_client import get_gmail_service, get_impersonate_user


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


def read_email(message_id: str, project: str = None):
    """Read full email content."""
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

        # Extract body
        body = ""
        payload = msg.get("payload", {})

        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8")
                        break
                elif part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8")
        else:
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8")

        output = {
            "id": message_id,
            "thread_id": msg.get("threadId"),
            "message_id_header": headers.get("Message-ID", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body,
            "labels": msg.get("labelIds", [])
        }

        print(json.dumps(output, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def create_draft(to: str, subject: str, body: str, cc: str = None,
                  thread_id: str = None, in_reply_to: str = None,
                  references: str = None, project: str = None):
    """Create email draft with YOUR formatting rules."""
    service = get_gmail_service(project=project)

    # Apply formatting rules
    html_body = format_body_html(body)

    # Create message
    message = MIMEMultipart("alternative")
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references

    # Attach HTML part (YOUR format rules applied)
    html_part = MIMEText(html_body, "html")
    message.attach(html_part)

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
               references: str = None, project: str = None):
    """Send email with YOUR formatting rules."""
    service = get_gmail_service(project=project)

    # Apply formatting rules
    html_body = format_body_html(body)

    # Create message
    message = MIMEMultipart("alternative")
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references

    # Attach HTML part
    html_part = MIMEText(html_body, "html")
    message.attach(html_part)

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
    group.add_argument("--draft", action="store_true", help="Create draft")
    group.add_argument("--send", action="store_true", help="Send email")
    group.add_argument("--labels", action="store_true", help="List labels")

    # Email arguments
    parser.add_argument("--to", help="Recipient email")
    parser.add_argument("--cc", help="CC recipients")
    parser.add_argument("--subject", help="Email subject")
    parser.add_argument("--body", help="Email body (plain text, will be formatted)")
    parser.add_argument("--max", type=int, default=10, help="Max search results (default: 10)")

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
                         references=references, project=args.project)
        else:
            send_email(to, subject, args.body, args.cc,
                       thread_id=thread_id, in_reply_to=in_reply_to,
                       references=references, project=args.project)

    elif args.labels:
        list_labels(project=args.project)


if __name__ == "__main__":
    main()
