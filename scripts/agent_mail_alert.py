#!/usr/bin/env python3
"""Send an alert email via Agent Mail.

Usage:
  agent_mail_alert.py --to roman@anarolabs.com \
                      --subject "[ALERT] ..." \
                      --body "Plain text body"
  agent_mail_alert.py --to roman@anarolabs.com \
                      --subject "[ALERT] ..." \
                      --body "Plain text fallback" \
                      --html "<p>Rich <b>HTML</b> body</p>"

Env:
  AGENTMAIL_API_KEY   (required) Bearer token from Agent Mail console
  AGENTMAIL_INBOX_ID  (required) Sender inbox, e.g. claude-code-local@agentmail.to

Exit codes:
  0 success
  1 failure (error to stderr)
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


API_BASE = "https://api.agentmail.to/v0"

# Disk fallback log - always written, even when email send fails or env vars missing.
# This is the recoverable record of every alert the system tried to send.
FAILURE_LOG = Path.home() / "Documents" / "Claude Code" / "athena" / "scan-failures.log"


def _load_dotenv_fallback():
    """Populate AGENTMAIL_* from portals/.env if not already in environment.

    Lets the alert path work when invoked outside launchd (manual debugging,
    cron-without-env, etc). No-op if vars are already set.
    """
    if os.getenv("AGENTMAIL_API_KEY") and os.getenv("AGENTMAIL_INBOX_ID"):
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key.startswith("AGENTMAIL_") and not os.getenv(key):
                os.environ[key] = value
    except Exception:
        pass  # silent - we still try to send and log to disk


def _log_to_disk(to, subject, body, status, detail=""):
    """Append every alert attempt to scan-failures.log. Never raises."""
    try:
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with FAILURE_LOG.open("a") as f:
            f.write(f"=== {ts} [{status}] ===\n")
            f.write(f"to: {to}\nsubject: {subject}\n")
            if detail:
                f.write(f"detail: {detail}\n")
            f.write(f"body:\n{body}\n\n")
    except Exception:
        pass


def send_alert(to, subject, body, html=None):
    _load_dotenv_fallback()
    api_key = os.getenv("AGENTMAIL_API_KEY")
    inbox_id = os.getenv("AGENTMAIL_INBOX_ID")
    if not api_key or not inbox_id:
        raise RuntimeError(
            "AGENTMAIL_API_KEY and AGENTMAIL_INBOX_ID must be set in environment "
            "(or in portals/.env)"
        )

    url = f"{API_BASE}/inboxes/{inbox_id}/messages/send"
    payload = {"to": to, "subject": subject, "text": body}
    if html:
        payload["html"] = html

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Send an alert email via Agent Mail")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--body", required=True, help="Plain text body")
    parser.add_argument("--html", default=None, help="Optional HTML body")
    args = parser.parse_args()

    try:
        result = send_alert(args.to, args.subject, args.body, args.html)
        print(json.dumps(result))
        _log_to_disk(args.to, args.subject, args.body, "SENT")
        return 0
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if e.fp else str(e)
        print(f"Agent Mail HTTP {e.code}: {detail}", file=sys.stderr)
        _log_to_disk(args.to, args.subject, args.body, "FAIL_HTTP", f"{e.code}: {detail}")
        return 1
    except Exception as e:
        print(f"Agent Mail error: {type(e).__name__}: {e}", file=sys.stderr)
        _log_to_disk(args.to, args.subject, args.body, "FAIL", f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
