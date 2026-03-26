#!/usr/bin/env python3
"""
Unified scanner for Anaro Labs and EstateMate projects.

Replaces 4 Claude Code sub-agents with direct API calls in parallel.
Writes results to .scan-cache.json, preserving processed_meetings and
deliverable_thread_bodies from existing cache.

Usage:
    python3 unified_scan.py --project anaro-labs [--force] [--skip-kg]
    python3 unified_scan.py --project estate-mate [--force] [--skip-kg]
    python3 unified_scan.py --project all [--force] [--skip-kg]
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "scan_config.json"
CACHE_FRESHNESS_SECONDS = 3600  # 60 minutes
MAX_DELIVERABLE_THREADS = 5


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def read_existing_cache(cache_path):
    """Read existing cache, preserving processed_meetings and deliverable_thread_bodies."""
    try:
        with open(cache_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_last_scan_timestamp(last_scan_path):
    """Read the last scan timestamp from file. Returns epoch seconds or None."""
    try:
        with open(last_scan_path) as f:
            content = f.read().strip()
            if content:
                return int(content)
    except (FileNotFoundError, ValueError):
        pass
    return None


def run_script(cmd, timeout=60):
    """Run a Python script and return parsed JSON output.

    Returns (data, error_message). On success, error_message is None.
    On failure, data is None and error_message describes what went wrong.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return None, f"Script exited with code {result.returncode}: {stderr[:500]}"

        stdout = result.stdout.strip()
        if not stdout:
            return None, "Script produced no output"

        try:
            data = json.loads(stdout)
            return data, None
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON output: {e}"

    except subprocess.TimeoutExpired:
        return None, f"Script timed out after {timeout}s"
    except FileNotFoundError:
        return None, f"Script not found: {cmd[0] if cmd else 'empty command'}"


# ---------------------------------------------------------------------------
# Scanner functions - each returns (section_name, data, error)
# ---------------------------------------------------------------------------

def scan_gmail(project_config, last_scan_ts):
    """Run the appropriate Gmail scanner."""
    gmail_cfg = project_config["gmail"]
    script = gmail_cfg["script"]

    # Default to 30 days ago if no last scan
    if last_scan_ts is None:
        last_scan_ts = int(time.time()) - (30 * 86400)

    cmd = ["python3", script, str(last_scan_ts)]

    data, err = run_script(cmd, timeout=30)
    if err:
        return "gmail", None, err

    # Normalize output format
    if gmail_cfg["type"] == "label_based":
        # AL format: {clients: {...}, partners: {...}}
        # Remove _db key if present (not needed in cache)
        data.pop("_db", None)
        return "gmail", data, None
    else:
        # EM format: {stakeholders: {...}, team: [...], unknown: [...], _meta: {...}}
        # Transform to cache format
        meta = data.get("_meta", {})
        stakeholder_counts = {}
        for name, emails in data.get("stakeholders", {}).items():
            stakeholder_counts[name] = len(emails) if isinstance(emails, list) else 0

        notable = []
        # Collect notable emails from stakeholders
        for name, emails in data.get("stakeholders", {}).items():
            if isinstance(emails, list):
                for email in emails:
                    if isinstance(email, dict):
                        notable.append(email)

        cache_data = {
            "stakeholder_counts": stakeholder_counts,
            "total_emails": meta.get("total_emails", len(notable)),
            "notable": notable,
            "last_scan_timestamp": int(time.time()),
            # Preserve raw data for deliverable matching
            "_raw_stakeholders": data.get("stakeholders", {}),
        }
        return "gmail", cache_data, None


def scan_linear_al(project_config):
    """Run AL Linear scanner (query-linear-issues.py - returns pre-categorized)."""
    script = project_config["linear"]["script"]
    cmd = ["python3", script]
    data, err = run_script(cmd, timeout=30)
    if err:
        return "linear", None, err
    return "linear", data, None


def scan_linear_em(project_config):
    """Run EM Linear scanner (linear_operations.py --list)."""
    script = project_config["linear"]["script"]
    cmd = ["python3", script, "--list", "--limit", "50"]
    data, err = run_script(cmd, timeout=30)
    if err:
        return "linear", None, err

    # Categorize flat issue list into buckets
    issues = data if isinstance(data, list) else data.get("issues", [])
    categorized = {
        "in_progress": [],
        "urgent": [],
        "high_priority": [],
        "recently_completed": [],
        "changes_since_last_scan": [],
    }

    for issue in issues:
        if not isinstance(issue, dict):
            continue

        status = (issue.get("status") or issue.get("state", {}).get("name", "")).lower()
        priority = issue.get("priority", 0)
        if isinstance(priority, str):
            priority_map = {"urgent": 1, "high": 2, "medium": 3, "low": 4, "none": 0}
            priority = priority_map.get(priority.lower(), 0)

        issue_summary = {
            "id": issue.get("identifier", issue.get("id", "")),
            "title": issue.get("title", ""),
            "status": status,
            "priority": priority,
            "assignee": issue.get("assignee", {}).get("name", "") if isinstance(issue.get("assignee"), dict) else "",
        }

        if status in ("in progress", "in_progress"):
            categorized["in_progress"].append(issue_summary)
        elif status in ("done", "completed"):
            categorized["recently_completed"].append(issue_summary)

        if priority == 1:
            categorized["urgent"].append(issue_summary)
        elif priority == 2:
            categorized["high_priority"].append(issue_summary)

    return "linear", categorized, None


def scan_linear(project_config):
    """Route to the correct Linear scanner."""
    linear_type = project_config["linear"]["type"]
    if linear_type == "dashboard":
        return scan_linear_al(project_config)
    else:
        return scan_linear_em(project_config)


def detect_linear_changes(new_data, old_data):
    """Compare new vs old Linear data to detect changes."""
    if not old_data or not new_data:
        return []

    changes = []

    # Build lookup of old issues by ID
    old_issues = {}
    for category in ("in_progress", "urgent", "high_priority", "recently_completed"):
        for issue in old_data.get(category, []):
            issue_id = issue.get("id", "")
            if issue_id:
                old_issues[issue_id] = {
                    "status": issue.get("status", ""),
                    "category": category,
                }

    # Compare against new issues
    new_issues = {}
    for category in ("in_progress", "urgent", "high_priority", "recently_completed"):
        for issue in new_data.get(category, []):
            issue_id = issue.get("id", "")
            if issue_id:
                new_issues[issue_id] = {
                    "status": issue.get("status", ""),
                    "category": category,
                    "title": issue.get("title", ""),
                }

    # Detect new issues
    for issue_id, info in new_issues.items():
        if issue_id not in old_issues:
            changes.append({
                "id": issue_id,
                "change": "new issue created",
                "title": info.get("title", ""),
            })
        else:
            old = old_issues[issue_id]
            if old["status"] != info["status"]:
                changes.append({
                    "id": issue_id,
                    "change": f"status: {old['status']} -> {info['status']}",
                    "title": info.get("title", ""),
                })

    # Detect completed issues
    for issue_id, old in old_issues.items():
        if issue_id not in new_issues:
            changes.append({
                "id": issue_id,
                "change": "completed or removed",
            })

    return changes


def scan_granola(project_config, last_scan_ts):
    """Run Granola scanner."""
    granola_cfg = project_config["granola"]
    script = granola_cfg["script"]

    # Default to 30 days ago
    if last_scan_ts is None:
        last_scan_ts = int(time.time()) - (30 * 86400)

    cmd = ["python3", script, str(last_scan_ts), "--include-content"]

    # Add --include-folders for EM (needed for filtering)
    filter_folders = granola_cfg.get("filter_folders")
    if filter_folders:
        cmd.append("--include-folders")

    data, err = run_script(cmd, timeout=45)
    if err:
        # Check for token expiry
        if err and "401" in str(err):
            return "granola", {
                "status": "token_expired",
                "error": "Granola token expired. Open the Granola app to refresh.",
            }, None  # Not a hard error - return status in data
        return "granola", None, err

    # Filter to EM-relevant meetings if needed
    if filter_folders:
        data = filter_granola_for_em(data, filter_folders, granola_cfg.get("known_domains"))

    return "granola", data, None


def filter_granola_for_em(data, folder_keywords, known_domains_path):
    """Filter Granola meetings to EstateMate-relevant ones."""
    # Load known domains for EM stakeholder matching
    em_domains = set()
    if known_domains_path:
        try:
            with open(known_domains_path) as f:
                kd = json.load(f)
                for section in kd.values():
                    if isinstance(section, dict):
                        for domains in section.values():
                            if isinstance(domains, list):
                                em_domains.update(domains)
                            elif isinstance(domains, dict):
                                for dl in domains.values():
                                    if isinstance(dl, list):
                                        em_domains.update(dl)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def is_em_meeting(meeting):
        """Check if a meeting belongs to EstateMate."""
        folder = (meeting.get("folder_name") or "").lower()
        for keyword in folder_keywords:
            if keyword.lower() in folder:
                return True
        # Check attendee domains
        domains = meeting.get("domains", [])
        if isinstance(domains, list):
            for d in domains:
                if d in em_domains:
                    return True
        return False

    # Restructure: filter all meetings, re-categorize
    filtered = {"stakeholders": {}, "team": [], "uncategorized": [], "_meta": {}}
    total = 0
    em_count = 0

    # Process all categories from unfiltered data
    all_meetings = []
    for category_key in ("clients", "partners", "stakeholders"):
        cat = data.get(category_key, {})
        if isinstance(cat, dict):
            for name, meetings in cat.items():
                if isinstance(meetings, list):
                    all_meetings.extend(meetings)
    all_meetings.extend(data.get("uncategorized", []))
    all_meetings.extend(data.get("team", []))

    total = len(all_meetings)

    for meeting in all_meetings:
        if is_em_meeting(meeting):
            em_count += 1
            # Categorize by attendee
            attendees = (meeting.get("attendees") or "").lower()
            if "ari" in attendees:
                filtered["stakeholders"].setdefault("Ari", []).append(meeting)
            elif "christof" in attendees or "chris" in attendees:
                filtered["team"].append(meeting)
            else:
                filtered["uncategorized"].append(meeting)

    filtered["_meta"] = {
        "total_meetings": total,
        "estatemate_meetings": em_count,
        "filtered_out": total - em_count,
    }

    return filtered


def scan_calendar(project_config):
    """Run Calendar scanner."""
    script = project_config["calendar"]["script"]
    cmd = ["python3", script, "--upcoming", "7"]
    data, err = run_script(cmd, timeout=15)
    if err:
        return "calendar", None, err

    # Wrap in expected format if it's a bare list
    if isinstance(data, list):
        events = data
        # Filter out all-day events without attendees
        meaningful = [e for e in events if not (
            e.get("all_day", False) and not e.get("attendees")
        )]
        data = {
            "events": meaningful,
            "_meta": {
                "total_events": len(events),
                "meetings_with_attendees": sum(
                    1 for e in events if e.get("attendees")
                ),
            },
        }

    return "calendar", data, None


# ---------------------------------------------------------------------------
# Deliverable thread matching
# ---------------------------------------------------------------------------

def normalize_subject(subject):
    """Strip reply/forward prefixes and normalize for matching."""
    subject = re.sub(r"^(Re|Fwd|FW|RE|FWD):\s*", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"^(Re|Fwd|FW|RE|FWD):\s*", "", subject, flags=re.IGNORECASE)
    return subject.strip().lower()


def tokenize(text):
    """Split text into lowercase word tokens."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def subjects_match(email_subject, issue_title):
    """Check if an email subject matches a Linear issue title."""
    norm_subject = normalize_subject(email_subject)
    norm_title = issue_title.lower().strip()

    # Exact substring match
    if norm_title in norm_subject or norm_subject in norm_title:
        return True

    # Word overlap: 60%+ of issue title words in email subject
    title_words = tokenize(norm_title)
    subject_words = tokenize(norm_subject)

    if not title_words:
        return False

    overlap = title_words & subject_words
    if len(overlap) / len(title_words) >= 0.6:
        return True

    return False


def match_deliverable_threads(gmail_data, linear_data, existing_threads, project_config):
    """Match email subjects against active Linear issues for thread fetching."""
    if not gmail_data or not linear_data:
        return []

    # Collect active issue titles
    active_issues = []
    for category in ("in_progress", "high_priority"):
        for issue in linear_data.get(category, []):
            active_issues.append({
                "id": issue.get("id", ""),
                "title": issue.get("title", ""),
            })

    if not active_issues:
        return []

    # Collect all emails with thread IDs
    all_emails = []
    gmail_type = project_config["gmail"]["type"]
    if gmail_type == "label_based":
        for category in ("clients", "partners"):
            cat = gmail_data.get(category, {})
            if isinstance(cat, dict):
                for name, emails in cat.items():
                    if isinstance(emails, list):
                        all_emails.extend(emails)
    else:
        # EM format
        raw = gmail_data.get("_raw_stakeholders", gmail_data.get("notable", []))
        if isinstance(raw, dict):
            for name, emails in raw.items():
                if isinstance(emails, list):
                    all_emails.extend(emails)
        elif isinstance(raw, list):
            all_emails.extend(raw)

    # Match emails against issues
    matched = {}
    for email in all_emails:
        if not isinstance(email, dict):
            continue
        subject = email.get("subject", "")
        thread_id = email.get("thread_id", email.get("threadId", ""))

        if not subject or not thread_id:
            continue

        # Skip if already fetched and no new messages
        if thread_id in (existing_threads or {}):
            existing = existing_threads[thread_id]
            if existing.get("message_count", 0) >= email.get("message_count", 0):
                continue

        for issue in active_issues:
            if subjects_match(subject, issue["title"]):
                if thread_id not in matched:
                    matched[thread_id] = {
                        "thread_id": thread_id,
                        "subject": subject,
                        "matched_issue": issue["id"],
                    }
                break

    # Limit to MAX_DELIVERABLE_THREADS
    return list(matched.values())[:MAX_DELIVERABLE_THREADS]


def fetch_thread_bodies(matches, project_config):
    """Fetch full thread bodies for matched deliverable threads."""
    if not matches:
        return {}

    fetcher_script = project_config.get("thread_fetcher", {}).get("script")
    if not fetcher_script:
        return {}

    results = {}
    for match in matches:
        thread_id = match["thread_id"]
        cmd = ["python3", fetcher_script, "--thread", thread_id]

        data, err = run_script(cmd, timeout=15)
        if err:
            print(f"  Warning: Failed to fetch thread {thread_id}: {err}", file=sys.stderr)
            continue

        results[thread_id] = {
            "subject": match["subject"],
            "matched_issue": match["matched_issue"],
            "fetched_at": int(time.time()),
            "message_count": data.get("message_count", len(data.get("messages", []))) if isinstance(data, dict) else 0,
            "signals": [],  # Signals are extracted by the skill, not the scanner
        }

    return results


# ---------------------------------------------------------------------------
# Cache write with file locking
# ---------------------------------------------------------------------------

def write_cache(cache_path, cache_data):
    """Write cache with file locking to prevent corruption."""
    cache_dir = os.path.dirname(cache_path)
    tmp_path = os.path.join(cache_dir, ".scan-cache.tmp")

    # Write to temp file first
    with open(tmp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(cache_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    # Atomic rename
    os.replace(tmp_path, cache_path)


# ---------------------------------------------------------------------------
# Main scan orchestration
# ---------------------------------------------------------------------------

def run_scan(project_name, config, force=False, skip_kg=False):
    """Run a complete scan for one project."""
    project_config = config[project_name]
    cache_path = project_config["cache_path"]
    last_scan_path = project_config["last_scan_path"]

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  Scanning: {project_name}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    # Read existing cache
    existing_cache = read_existing_cache(cache_path)
    last_scan_ts = get_last_scan_timestamp(last_scan_path)

    # Check freshness
    if not force and last_scan_ts:
        age = int(time.time()) - last_scan_ts
        if age < CACHE_FRESHNESS_SECONDS:
            age_min = age // 60
            print(f"  Cache fresh ({age_min} min old). Use --force to re-scan.", file=sys.stderr)
            return

    print(f"  Last scan: {last_scan_ts or 'never'}", file=sys.stderr)

    # Run 4 scanners in parallel
    scan_results = {}
    scan_errors = {}
    scanner_durations = {}
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=4) as executor:
        scanner_start_times = {}
        futures = {
            executor.submit(scan_gmail, project_config, last_scan_ts): "gmail",
            executor.submit(scan_linear, project_config): "linear",
            executor.submit(scan_granola, project_config, last_scan_ts): "granola",
            executor.submit(scan_calendar, project_config): "calendar",
        }
        for f in futures:
            scanner_start_times[f] = time.time()

        for future in as_completed(futures):
            scanner_name = futures[future]
            scanner_elapsed = time.time() - scanner_start_times[future]
            try:
                section, data, err = future.result()
                scanner_durations[section] = round(scanner_elapsed, 2)
                if err:
                    scan_errors[section] = err
                    print(f"  ERROR [{section}]: {err}", file=sys.stderr)
                elif data:
                    scan_results[section] = data
                    print(f"  OK [{section}] ({scanner_elapsed:.1f}s)", file=sys.stderr)
                else:
                    print(f"  EMPTY [{section}]", file=sys.stderr)
            except Exception as e:
                scan_errors[scanner_name] = str(e)
                print(f"  EXCEPTION [{scanner_name}]: {e}", file=sys.stderr)

    elapsed = time.time() - start_time
    print(f"\n  Scans completed in {elapsed:.1f}s", file=sys.stderr)
    print(f"  Success: {len(scan_results)}, Errors: {len(scan_errors)}", file=sys.stderr)

    # Detect Linear changes
    if "linear" in scan_results:
        old_linear = existing_cache.get("linear", {})
        changes = detect_linear_changes(scan_results["linear"], old_linear)
        scan_results["linear"]["changes_since_last_scan"] = changes
        if changes:
            print(f"  Linear changes detected: {len(changes)}", file=sys.stderr)

    # Match deliverable threads
    existing_threads = existing_cache.get("deliverable_thread_bodies", {})
    matches = match_deliverable_threads(
        scan_results.get("gmail"),
        scan_results.get("linear"),
        existing_threads,
        project_config,
    )

    # Fetch matched thread bodies
    new_threads = {}
    if matches:
        print(f"  Fetching {len(matches)} deliverable thread(s)...", file=sys.stderr)
        new_threads = fetch_thread_bodies(matches, project_config)
        print(f"  Fetched {len(new_threads)} thread(s)", file=sys.stderr)

    # Build updated cache
    now_ts = int(time.time())
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cache_data = {
        "scan_timestamp": now_ts,
        "scan_timestamp_iso": now_iso,
        "scan_version": 2,
        "scanner": "unified_scan.py",
    }

    # Preserve existing deliverable_thread_bodies, merge new ones
    merged_threads = dict(existing_threads)
    merged_threads.update(new_threads)
    cache_data["deliverable_thread_bodies"] = merged_threads

    # Preserve processed_meetings from existing cache
    cache_data["processed_meetings"] = existing_cache.get("processed_meetings", {})

    # Add scan results (use existing data as fallback for failed scanners)
    for section in ("gmail", "granola", "linear", "calendar"):
        if section in scan_results:
            cache_data[section] = scan_results[section]
        elif section in existing_cache:
            cache_data[section] = existing_cache[section]
            print(f"  Using cached data for [{section}] (scanner failed)", file=sys.stderr)

    # Record scan errors in cache for skill visibility
    if scan_errors:
        cache_data["_scan_errors"] = {
            k: v for k, v in scan_errors.items()
        }

    # Remove internal keys not needed in cache
    if "gmail" in cache_data and "_raw_stakeholders" in cache_data.get("gmail", {}):
        cache_data["gmail"].pop("_raw_stakeholders", None)

    # Write cache
    write_cache(cache_path, cache_data)
    print(f"  Cache written: {cache_path}", file=sys.stderr)

    # Write last-scan timestamp
    with open(last_scan_path, "w") as f:
        f.write(str(now_ts))

    # Optional: KG ingestion
    kg_duration = None
    if not skip_kg:
        kg_script = Path(__file__).parent.parent.parent / "knowledge-graph" / "03-implementation" / "ingestion" / "kg_ingest.py"
        if kg_script.exists():
            neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
            is_http = neo4j_uri.startswith("http://") or neo4j_uri.startswith("https://")

            # For local Bolt mode, ensure Neo4j Docker is running
            if not is_http:
                compose_file = Path(__file__).parent.parent.parent / "knowledge-graph" / "docker-compose.yml"
                if compose_file.exists():
                    subprocess.run(
                        ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                        capture_output=True, timeout=30,
                    )

            print(f"  Running KG ingestion...", file=sys.stderr)
            kg_start = time.time()

            # HTTP mode uses stdlib only (no neo4j package needed)
            if is_http:
                cmd = ["python3", str(kg_script), "--project", project_name, "--cache-file", cache_path]
            else:
                cmd = ["uv", "run", "--with", "neo4j", "python3", str(kg_script), "--project", project_name, "--cache-file", cache_path]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            kg_duration = round(time.time() - kg_start, 2)
            if result.returncode == 0:
                print(f"  KG ingestion complete ({kg_duration:.1f}s)", file=sys.stderr)
            else:
                print(f"  KG ingestion failed: {result.stderr[:500]}", file=sys.stderr)
        else:
            pass  # KG not yet installed, skip silently

    # Optional: Markdown indexing
    md_duration = None
    if not skip_kg:  # Reuse the same flag - if KG is enabled, indexing is too
        md_script = Path(__file__).parent.parent.parent / "knowledge-graph" / "03-implementation" / "ingestion" / "md_indexer.py"
        if md_script.exists():
            print(f"  Running Markdown indexer...", file=sys.stderr)
            md_start = time.time()

            # MD indexer uses neo4j_client.py which auto-detects transport
            md_env = os.environ.copy()
            md_env["TRACE_ID"] = os.environ.get("TRACE_ID", "")

            if is_http:
                md_cmd = ["python3", str(md_script)]
            else:
                md_cmd = ["uv", "run", "--with", "neo4j", "python3", str(md_script)]

            result = subprocess.run(
                md_cmd,
                capture_output=True,
                text=True,
                timeout=180,
                env=md_env,
            )
            md_duration = round(time.time() - md_start, 2)
            if result.returncode == 0:
                print(f"  Markdown indexer complete ({md_duration:.1f}s)", file=sys.stderr)
            else:
                print(f"  Markdown indexer failed: {result.stderr[:500]}", file=sys.stderr)

    # Record scan run in unified tracing (spans.db)
    try:
        cockpit_path = str(Path(__file__).parent.parent.parent / "athena")
        sys.path.insert(0, cockpit_path)
        from spans import start_trace, start_span, end_span

        trace_id = os.environ.get("TRACE_ID")
        trigger = "skill" if trace_id else ("launchd" if os.environ.get("LAUNCHED_BY_LAUNCHD") else "manual")

        # Create root span if not part of a skill trace
        if not trace_id:
            trace_id = start_trace("scan:unified", kind="scan", project=project_name, trigger=trigger)

        scan_span = start_span("scan:unified", kind="scan", parent=trace_id, project=project_name, trigger=trigger)

        # Record individual scanner spans
        for scanner_name, duration in scanner_durations.items():
            s = start_span(f"scan:{scanner_name}", kind="scan", parent=scan_span, project=project_name)
            scanner_metrics = {}
            if scanner_name in scan_results:
                data = scan_results[scanner_name]
                if scanner_name == "gmail":
                    scanner_metrics["emails"] = data.get("total_emails", 0)
                elif scanner_name == "linear":
                    scanner_metrics["changes"] = len(data.get("changes_since_last_scan", []))
                elif scanner_name == "granola":
                    meta = data.get("_meta", {})
                    scanner_metrics["meetings"] = meta.get("total_meetings", 0)
                elif scanner_name == "calendar":
                    meta = data.get("_meta", {})
                    scanner_metrics["events"] = meta.get("total_events", 0)
            status = "error" if scanner_name in scan_errors else "ok"
            error = scan_errors.get(scanner_name)
            end_span(s, status=status, metrics=scanner_metrics, error=error)

        # Record KG ingestion span
        if kg_duration is not None:
            kg_span = start_span("ingest:kg", kind="ingest", parent=scan_span, project=project_name)
            end_span(kg_span, status="ok", metrics={"duration_s": kg_duration})

        # End the scan span
        scan_metrics = {
            "scanners": len(scan_results),
            "errors": len(scan_errors),
            "elapsed_s": round(elapsed, 1),
        }
        for name, dur in scanner_durations.items():
            scan_metrics[f"duration_{name}"] = dur
        if kg_duration:
            scan_metrics["duration_kg"] = kg_duration

        end_span(scan_span, status="ok" if not scan_errors else "partial", metrics=scan_metrics)

        # Set TRACE_ID for any downstream subprocesses
        os.environ["TRACE_ID"] = trace_id
    except Exception as e:
        print(f"  Trace write skipped: {e}", file=sys.stderr)

    # Output summary as JSON to stdout
    summary = {
        "project": project_name,
        "scan_timestamp": now_ts,
        "scan_timestamp_iso": now_iso,
        "scanners": {
            section: "ok" for section in scan_results
        },
        "errors": scan_errors,
        "deliverable_threads_fetched": len(new_threads),
        "linear_changes": len(scan_results.get("linear", {}).get("changes_since_last_scan", [])),
        "elapsed_seconds": round(elapsed, 1),
    }
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Unified project scanner")
    parser.add_argument(
        "--project",
        required=True,
        choices=["anaro-labs", "estate-mate", "all"],
        help="Project to scan",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass cache freshness check",
    )
    parser.add_argument(
        "--skip-kg",
        action="store_true",
        help="Skip knowledge graph ingestion",
    )
    args = parser.parse_args()

    config = load_config()

    # Validate config paths exist
    projects = ["anaro-labs", "estate-mate"] if args.project == "all" else [args.project]
    for project in projects:
        pc = config[project]
        for scanner in ("gmail", "linear", "granola", "calendar"):
            script_path = pc[scanner].get("script", pc[scanner]) if isinstance(pc[scanner], dict) else pc[scanner]
            if isinstance(script_path, str) and not os.path.exists(script_path):
                print(f"WARNING: Script not found: {script_path}", file=sys.stderr)

    for project in projects:
        run_scan(project, config, args.force, args.skip_kg)


if __name__ == "__main__":
    main()
