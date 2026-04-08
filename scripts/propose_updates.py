#!/usr/bin/env python3
"""
Proposal generator for update skills.

Reads .scan-cache.json, diffs against processed trackers, and generates
structured proposals in .pending-updates.json. Pure Python, no LLM.

The interactive update skills (/anarolabs-update, /estatemate-update)
consume these proposals instead of analyzing raw scan data.

Usage:
    python3 propose_updates.py --project anaro-labs
    python3 propose_updates.py --project estate-mate
    python3 propose_updates.py --project all
"""

import argparse
import fcntl
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "scan_config.json"

# Limits
MAX_PROPOSALS = 100
MEETING_LOOKBACK_DAYS = 7
EMAIL_LOOKBACK_DAYS = 14
STALE_PROPOSAL_DAYS = 7

# Athena integration (optional)
try:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "athena"))
    from spans import start_trace, start_span, end_span
    ATHENA_AVAILABLE = True
except ImportError:
    ATHENA_AVAILABLE = False


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def read_json(path):
    """Safely read a JSON file, returning empty dict on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_pending(path, data):
    """Write pending updates with file locking and atomic rename."""
    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    os.rename(tmp_path, path)


def gen_id():
    return f"prop-{uuid.uuid4().hex[:12]}"


def now_ts():
    return int(time.time())


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_known_domains(project_config):
    """Load known-domains.json for entity-to-folder mapping."""
    granola_cfg = project_config.get("granola", {})
    domains_path = granola_cfg.get("known_domains")
    if domains_path:
        return read_json(domains_path)
    return {}


def diff_meetings(cache, tracker, known_domains):
    """Find new or updated meetings not yet proposed."""
    proposals = []
    granola = cache.get("granola", {})

    # Build entity lookup from known-domains
    entity_folders = {}
    for category in ("clients", "partners"):
        for entity_name, entity_config in known_domains.get(category, {}).items():
            if isinstance(entity_config, dict):
                entity_folders[entity_name] = {
                    "folder": entity_config.get("folder", ""),
                    "category": category,
                }

    processed = tracker.get("meetings", {})

    for category in ("clients", "partners"):
        section = granola.get(category, {})
        if not isinstance(section, dict):
            continue
        for entity_name, meetings in section.items():
            if not isinstance(meetings, list):
                continue
            for meeting in meetings:
                doc_id = meeting.get("granola_doc_id") or meeting.get("id")
                if not doc_id:
                    continue

                # Skip if already processed
                if doc_id in processed:
                    processed_ts = processed[doc_id]
                    if isinstance(processed_ts, dict):
                        processed_ts = processed_ts.get("processed_at", 0)
                    # Skip if processed after meeting date
                    meeting_ts = meeting.get("timestamp", 0)
                    if isinstance(meeting_ts, str):
                        try:
                            meeting_ts = int(datetime.fromisoformat(
                                meeting_ts.replace("Z", "+00:00")
                            ).timestamp())
                        except (ValueError, TypeError):
                            meeting_ts = 0
                    if processed_ts > meeting_ts:
                        continue

                entity_info = entity_folders.get(entity_name, {})
                target_folder = entity_info.get("folder", "")
                target_file = f"{target_folder}/STATUS.md" if target_folder else ""

                proposals.append({
                    "id": gen_id(),
                    "source_type": "meeting",
                    "source_id": doc_id,
                    "source_label": f"{meeting.get('title', 'Unknown meeting')} ({meeting.get('date', 'unknown date')})",
                    "decision_type": "meeting_entity",
                    "target_entity": entity_name,
                    "target_file": target_file,
                    "change_type": "update",
                    "summary": f"Meeting with {entity_name}: {meeting.get('title', '')}",
                    "raw_data_ref": {
                        "cache_section": "granola",
                        "category": category,
                        "entity": entity_name,
                    },
                    "priority": "normal",
                    "created_at": now_iso(),
                })

    # Uncategorized meetings
    for meeting in granola.get("uncategorized", []):
        doc_id = meeting.get("granola_doc_id") or meeting.get("id")
        if not doc_id:
            continue
        if doc_id in processed:
            continue

        domains = meeting.get("domains", [])
        domain_str = ", ".join(domains) if domains else "unknown"

        proposals.append({
            "id": gen_id(),
            "source_type": "meeting",
            "source_id": doc_id,
            "source_label": f"{meeting.get('title', 'Unknown meeting')} ({meeting.get('date', 'unknown date')})",
            "decision_type": "meeting_uncategorized",
            "target_entity": None,
            "target_file": "",
            "change_type": "create",
            "summary": f"Uncategorized meeting: {meeting.get('title', '')} (domains: {domain_str})",
            "raw_data_ref": {
                "cache_section": "granola",
                "category": "uncategorized",
            },
            "priority": "normal",
            "created_at": now_iso(),
        })

    return proposals


def diff_emails(cache, tracker, known_domains):
    """Find email threads with new activity."""
    proposals = []
    processed = tracker.get("emails", {})

    # Check deliverable threads (matched to Linear issues)
    threads = cache.get("deliverable_thread_bodies", {})
    for thread_id, thread_data in threads.items():
        if not isinstance(thread_data, dict):
            continue

        subject = thread_data.get("subject", "")
        message_count = thread_data.get("message_count", 0)
        signals = thread_data.get("signals", [])
        matched_issue = thread_data.get("matched_issue", "")

        # Check if new or updated
        prev = processed.get(thread_id, {})
        if isinstance(prev, (int, float)):
            prev = {"processed_at": prev, "message_count": 0}

        prev_count = prev.get("message_count", 0)
        if message_count <= prev_count and thread_id in processed:
            continue  # No new messages

        new_signals = len(signals) - prev.get("signal_count", 0)
        if new_signals <= 0 and thread_id in processed:
            continue  # No new signals

        proposals.append({
            "id": gen_id(),
            "source_type": "email",
            "source_id": thread_id,
            "source_label": f"{subject} (thread, {message_count} msgs)",
            "decision_type": "email_update",
            "target_entity": _entity_from_issue(matched_issue) if matched_issue else None,
            "target_file": "",
            "change_type": "update",
            "summary": f"Deliverable thread with {new_signals} new signal(s) since last processing",
            "raw_data_ref": {
                "cache_section": "deliverable_thread_bodies",
                "key": thread_id,
            },
            "priority": "high" if new_signals > 2 else "normal",
            "created_at": now_iso(),
        })

    # Check gmail client/partner emails for new notable items
    gmail = cache.get("gmail", {})
    for category in ("clients", "partners"):
        section = gmail.get(category, {})
        if not isinstance(section, dict):
            continue
        for entity_name, entity_data in section.items():
            if not isinstance(entity_data, dict):
                continue
            notable = entity_data.get("notable", [])
            for email in notable:
                msg_id = email.get("id") or email.get("message_id")
                if not msg_id:
                    continue
                if msg_id in processed:
                    continue

                subject = email.get("subject", "No subject")
                proposals.append({
                    "id": gen_id(),
                    "source_type": "email",
                    "source_id": msg_id,
                    "source_label": f"{subject}",
                    "decision_type": "email",
                    "target_entity": entity_name,
                    "target_file": "",
                    "change_type": "update",
                    "summary": f"Notable email from {entity_name}: {subject}",
                    "raw_data_ref": {
                        "cache_section": "gmail",
                        "category": category,
                        "entity": entity_name,
                    },
                    "priority": "normal",
                    "created_at": now_iso(),
                })

    return proposals


def _entity_from_issue(issue_key):
    """Best-effort entity mapping from Linear issue key."""
    # GYG WFM issues are the most common
    if issue_key.startswith("ANA-"):
        return "GYG"  # Most ANA issues are GYG-related; skill can refine
    if issue_key.startswith("EST-"):
        return "EstateMate"
    return None


def diff_linear(cache, tracker):
    """Find Linear issues that changed since last scan."""
    proposals = []
    linear = cache.get("linear", {})
    changes = linear.get("changes_since_last_scan", [])
    processed = tracker.get("linear_issues", {})

    for change in changes:
        issue_key = change.get("identifier", "")
        if not issue_key:
            continue
        if issue_key in processed:
            # Check if this is a newer change
            prev_ts = processed[issue_key]
            if isinstance(prev_ts, dict):
                prev_ts = prev_ts.get("processed_at", 0)
            # Simple skip if already processed
            continue

        title = change.get("title", "")
        change_type = change.get("change_type", "updated")
        old_state = change.get("old_state", "")
        new_state = change.get("new_state", "")

        summary = f"Linear {issue_key}: {title}"
        if old_state and new_state:
            summary += f" ({old_state} -> {new_state})"

        proposals.append({
            "id": gen_id(),
            "source_type": "linear",
            "source_id": issue_key,
            "source_label": f"{issue_key}: {title}",
            "decision_type": "linear_stale" if change_type == "status" else "status_drift",
            "target_entity": _entity_from_issue(issue_key),
            "target_file": "",
            "change_type": change_type,
            "summary": summary,
            "raw_data_ref": {
                "cache_section": "linear",
                "key": "changes_since_last_scan",
            },
            "priority": "normal",
            "created_at": now_iso(),
        })

    return proposals


def dedup_proposals(new_proposals, existing_proposals):
    """Merge new proposals with existing, deduplicating by (source_type, source_id).

    If a proposal for the same source already exists, update it (latest wins).
    """
    # Index existing by (source_type, source_id)
    existing_index = {}
    for p in existing_proposals:
        key = (p.get("source_type"), p.get("source_id"))
        existing_index[key] = p

    # Overlay new proposals
    for p in new_proposals:
        key = (p.get("source_type"), p.get("source_id"))
        existing_index[key] = p

    # Return as list, sorted by priority then created_at
    priority_order = {"high": 0, "normal": 1, "low": 2}
    result = sorted(
        existing_index.values(),
        key=lambda p: (priority_order.get(p.get("priority", "normal"), 1), p.get("created_at", "")),
    )

    return result[:MAX_PROPOSALS]


def prune_stale(proposals):
    """Remove proposals older than STALE_PROPOSAL_DAYS."""
    cutoff = now_ts() - (STALE_PROPOSAL_DAYS * 86400)
    pruned = []
    for p in proposals:
        created = p.get("created_at", "")
        if created:
            try:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
        pruned.append(p)
    return pruned


def propose_for_project(project_name, project_config, force=False):
    """Generate proposals for a single project."""
    cache_path = project_config["cache_path"]
    project_root = project_config["project_root"]
    pending_path = os.path.join(project_root, ".pending-updates.json")

    # Read inputs
    cache = read_json(cache_path)
    if not cache:
        print(f"  No scan cache found at {cache_path}", file=sys.stderr)
        return 0

    existing_pending = read_json(pending_path)
    known_domains = load_known_domains(project_config)

    # Get processed trackers (from pending file, fall back to scan cache)
    tracker = existing_pending.get("processed_tracker", {})
    if not tracker.get("meetings"):
        # Migration: read from scan cache
        scan_meetings = cache.get("processed_meetings", {})
        if scan_meetings:
            tracker["meetings"] = {
                k: (v.get("processed_at", 0) if isinstance(v, dict) else v)
                for k, v in scan_meetings.items()
            }
    if not tracker.get("emails"):
        tracker["emails"] = {}
    if not tracker.get("linear_issues"):
        tracker["linear_issues"] = {}

    # Start Athena trace
    trace_id = None
    if ATHENA_AVAILABLE:
        trace_id = start_trace(
            "propose:unified", kind="propose",
            project=project_name, trigger="scheduled"
        )

    # Generate proposals from each source
    meeting_proposals = diff_meetings(cache, tracker, known_domains)
    email_proposals = diff_emails(cache, tracker, known_domains)
    linear_proposals = diff_linear(cache, tracker)

    all_new = meeting_proposals + email_proposals + linear_proposals

    # Dedup against existing proposals (not yet acted on)
    existing_proposals = existing_pending.get("proposals", [])
    existing_proposals = prune_stale(existing_proposals)
    merged = dedup_proposals(all_new, existing_proposals)

    # Write Athena spans for new proposals
    if ATHENA_AVAILABLE and trace_id:
        for p in all_new:
            span_id = start_span(
                f"propose:{p['source_type']}", kind="propose",
                parent=trace_id, project=project_name
            )
            end_span(span_id, status="ok", metrics={
                "source_type": p["source_type"],
                "source_id": p["source_id"],
                "target_entity": p.get("target_entity"),
                "decision_type": p["decision_type"],
                "change_type": p["change_type"],
            }, comment=p["summary"])
            p["athena_span_id"] = span_id

    # Build output
    output = {
        "generated_at": now_ts(),
        "generated_at_iso": now_iso(),
        "project": project_name,
        "scan_timestamp": cache.get("scan_timestamp", 0),
        "proposals": merged,
        "processed_tracker": tracker,
        "stats": {
            "meetings_new": len(meeting_proposals),
            "emails_new": len(email_proposals),
            "linear_changes": len(linear_proposals),
            "proposals_total": len(merged),
            "proposals_generated": len(all_new),
        },
    }

    # Write pending file
    write_pending(pending_path, output)

    # End Athena trace
    if ATHENA_AVAILABLE and trace_id:
        end_span(trace_id, status="ok", metrics=output["stats"])

    # Report
    print(f"  Proposals: {len(all_new)} new, {len(merged)} total", file=sys.stderr)
    print(f"    Meetings: {len(meeting_proposals)}, Emails: {len(email_proposals)}, Linear: {len(linear_proposals)}", file=sys.stderr)
    print(f"  Written: {pending_path}", file=sys.stderr)

    return len(all_new)


def main():
    parser = argparse.ArgumentParser(description="Generate update proposals from scan cache")
    parser.add_argument("--project", required=True, help="Project name or 'all'")
    parser.add_argument("--force", action="store_true", help="Force regeneration")
    args = parser.parse_args()

    config = load_config()

    if args.project == "all":
        projects = list(config.keys())
    else:
        if args.project not in config:
            print(f"Unknown project: {args.project}", file=sys.stderr)
            sys.exit(1)
        projects = [args.project]

    total = 0
    for project_name in projects:
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Proposing: {project_name}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        total += propose_for_project(project_name, config[project_name], args.force)

    if total == 0:
        print("\nNo new proposals generated", file=sys.stderr)


if __name__ == "__main__":
    main()
