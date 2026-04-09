#!/usr/bin/env python3
"""Sentinel — the watcher at the gate of the House of Prometheus.

The Sentinel is the entry point of every household cycle. It runs the
periodic scan of Gmail, Granola, Calendar, and Drive sources, then
orchestrates the rest of the household members in sequence: Scribe →
Author → Foreman → Librarian → Cartographer → Custodian → Herald.

USAGE
-----

Full cycle (this runs every 45 minutes via launchd):
    python3 sentinel.py --full --project anaro-labs

Scan only (no member spawning):
    python3 sentinel.py --scan-only --project anaro-labs

Status:
    python3 sentinel.py status

Per-member partial runs:
    python3 sentinel.py --scribe-only --project anaro-labs
    python3 sentinel.py --author-only --project anaro-labs
    python3 sentinel.py --foreman-only --project anaro-labs

ARCHITECTURE
------------

This sentinel.py is a thin orchestrator. Its job is to:

1. Wrap the entire cycle in an Athena root trace
2. Run the scan layer (delegated to the existing `unified_scan.py`, which
   already implements the parallel multi-source scan)
3. Read the scan cache and identify what each downstream member needs to
   process
4. For each downstream member, log a span and either:
   a. Invoke the member's CLI helper (deterministic helpers only), OR
   b. Write a "ready for processing" record that a Claude Code subagent
      session will pick up

For LLM-driven steps (Scribe extraction, Author patching with content
reasoning, Foreman title generation, Cartographer entity resolution),
the Sentinel writes a queue and the actual reasoning happens when the
Steward or member subagent is spawned via the Claude Code Agent tool.
The Sentinel does NOT call Anthropic SDK directly.

For the cron path (launchd), the Sentinel completes the deterministic
work and surfaces the "needs subagent" queue in `state/sentinel-queue.md`.
A future iteration will integrate Claude Code's headless mode to spawn
subagents from Python.

The legacy `unified_scan.py` remains in place. The Sentinel calls it as
a subprocess for the scan layer; this preserves the 995-line scanner
without rewriting it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the athena module importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATHENA_DIR = _REPO_ROOT / "athena"
if _ATHENA_DIR.exists() and str(_ATHENA_DIR) not in sys.path:
    sys.path.insert(0, str(_ATHENA_DIR))

# Make the household member modules importable
_INGESTION_DIR = _REPO_ROOT / "knowledge-graph" / "03-ingestion"
if _INGESTION_DIR.exists() and str(_INGESTION_DIR) not in sys.path:
    sys.path.insert(0, str(_INGESTION_DIR))

UNIFIED_SCAN = Path(__file__).resolve().parent / "unified_scan.py"
SENTINEL_STATE_DIR = _INGESTION_DIR / "state"
SENTINEL_QUEUE = SENTINEL_STATE_DIR / "sentinel-queue.md"
SCAN_CACHE = Path(__file__).resolve().parent / ".scan-cache.json"


# ---------------------------------------------------------------------------
# Athena helpers (with graceful degradation if not importable)
# ---------------------------------------------------------------------------


def _athena():
    """Return the spans module if importable, else None."""
    try:
        import spans  # type: ignore
        return spans
    except ImportError:
        return None


def _start_trace(name: str, project: str, trigger: str) -> str | None:
    spans = _athena()
    if not spans:
        return None
    return spans.start_trace(name=name, kind="skill", project=project, trigger=trigger)


def _start_span(name: str, parent: str | None, project: str) -> str | None:
    spans = _athena()
    if not spans:
        return None
    return spans.start_span(name=name, kind="agent", parent=parent, project=project)


def _end_span(span_id: str | None, status: str, metrics: dict[str, Any] | None = None,
               raw_output: str | None = None, comment: str | None = None) -> None:
    spans = _athena()
    if not spans or not span_id:
        return
    spans.end_span(span_id, status=status, metrics=metrics or {},
                   raw_output=raw_output, comment=comment)


# ---------------------------------------------------------------------------
# Sentinel scan layer (delegates to unified_scan.py)
# ---------------------------------------------------------------------------


def run_unified_scan(*, project: str, force: bool = False, skip_kg: bool = True) -> dict[str, Any]:
    """Invoke unified_scan.py as a subprocess and return its result.

    Returns a dict with `ok`, `returncode`, `stdout_tail`, and any error.
    The unified scanner writes to .scan-cache.json directly; the sentinel
    reads that cache afterward.
    """
    if not UNIFIED_SCAN.exists():
        return {"ok": False, "error": f"unified_scan.py not found at {UNIFIED_SCAN}"}

    args = ["python3", str(UNIFIED_SCAN), "--project", project]
    if force:
        args.append("--force")
    if skip_kg:
        args.append("--skip-kg")

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "unified_scan timed out after 300s"}
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"python3 not found: {exc}"}

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-20:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-20:]),
    }


def read_scan_cache() -> dict[str, Any]:
    """Read .scan-cache.json if it exists."""
    if not SCAN_CACHE.exists():
        return {}
    try:
        with SCAN_CACHE.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Pipeline orchestration: write a queue file the next subagent picks up
# ---------------------------------------------------------------------------


def write_queue(*, project: str, scan_summary: dict[str, Any], trace_id: str | None) -> Path:
    """Write a sentinel queue markdown file the Steward subagent reads.

    The queue describes what each downstream member should process. The
    Steward (or Sentinel orchestrator) reads this file when it spawns
    member subagents via the Claude Code Agent tool.
    """
    SENTINEL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()

    parts = [
        "---",
        "role: meta",
        f"created: {timestamp}",
        f"updated: {timestamp}",
        "version: 1.0",
        "status: active",
        f"project: {project}",
        f"trace_id: {trace_id or 'none'}",
        "produced_by: sentinel",
        "---",
        "",
        f"# Sentinel queue ({project})",
        "",
        f"Cycle started at {timestamp}.",
        "",
        "## Scan summary",
        "",
        f"```json",
        json.dumps(scan_summary, indent=2, default=str)[:2000],
        "```",
        "",
        "## Next steps",
        "",
        "When the Steward subagent (or a Claude Code session) reads this file, it should:",
        "",
        "1. Spawn the **Scribe** subagent to extract structured statements from the new sources listed above",
        "2. Pass Scribe output to the **Author** subagent for CONTEXT.md patching and decision file creation",
        "3. Pass commitment statements to the **Foreman** subagent for Linear sync",
        "4. Run the **Librarian** to classify any new files",
        "5. Run the **Cartographer** to rebuild the KG from updated markdown + git + Linear",
        "6. Run the **Custodian** to validate skills and references",
        "7. Run the **Herald** to write the daily digest and update needs-review.md",
        "",
        "## Why this is a queue, not a direct invocation",
        "",
        "The Sentinel runs as a launchd subprocess every 45 minutes and cannot",
        "directly spawn Claude Code subagents (which require an interactive",
        "session or the Agent SDK). The Sentinel produces the queue; the",
        "Steward or a manual `claude code` session picks it up and orchestrates",
        "the LLM-driven members.",
        "",
        "Alternatively, run the deterministic CLI helpers directly via:",
        "",
        "```",
        "cd \"/Users/romansiepelmeyer/Documents/Claude Code/knowledge-graph/03-ingestion\"",
        "python3 scribe.py status",
        "python3 author.py status",
        "python3 foreman.py status",
        "python3 librarian.py classify-tree --root \"/Users/romansiepelmeyer/Documents/Claude Code/anaro-labs\"",
        "uv run --with neo4j python3 cartographer.py count",
        "python3 custodian.py sweep",
        "```",
    ]

    SENTINEL_QUEUE.write_text("\n".join(parts), encoding="utf-8")
    return SENTINEL_QUEUE


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_full(args: argparse.Namespace) -> int:
    project = args.project
    trace_id = _start_trace(name="prometheus:cycle", project=project, trigger="cron")

    # Step 1: Sentinel scan
    scan_span = _start_span(name="agent:sentinel:scan", parent=trace_id, project=project)
    scan_result = run_unified_scan(project=project, force=args.force, skip_kg=args.skip_kg)
    _end_span(
        scan_span,
        status="ok" if scan_result.get("ok") else "error",
        metrics={"member": "sentinel", "scan_returncode": scan_result.get("returncode", -1)},
        raw_output=json.dumps(scan_result, default=str),
    )

    if not scan_result.get("ok"):
        # End the root trace as a failure
        _end_span(
            trace_id,
            status="error",
            metrics={"members_invoked": 1, "scan_failed": True},
            comment=f"Sentinel scan failed: {scan_result.get('error') or scan_result.get('stderr_tail')}",
        )
        print(json.dumps({"status": "scan_failed", "result": scan_result}, indent=2))
        return 1

    # Step 2: read cache and build scan summary
    cache = read_scan_cache()
    scan_summary = {
        "cache_keys": list(cache.keys())[:20],
        "cache_size_bytes": SCAN_CACHE.stat().st_size if SCAN_CACHE.exists() else 0,
        "scan_returncode": scan_result.get("returncode"),
        "stdout_tail": scan_result.get("stdout_tail", "")[:500],
    }

    # Step 3: write the sentinel queue for downstream members
    queue_span = _start_span(name="agent:sentinel:queue", parent=trace_id, project=project)
    queue_path = write_queue(project=project, scan_summary=scan_summary, trace_id=trace_id)
    _end_span(
        queue_span,
        status="ok",
        metrics={"member": "sentinel", "queue_path": str(queue_path)},
    )

    # Close the root trace. Note: the rest of the household members
    # (Scribe, Author, Foreman, Cartographer, Librarian, Custodian, Herald)
    # are NOT spawned by this Python script. They are spawned by the
    # Steward subagent in a Claude Code session that reads the queue.
    _end_span(
        trace_id,
        status="partial",
        metrics={
            "members_invoked": 1,
            "queue_written": True,
            "downstream_members_pending": 7,
        },
        comment="Sentinel scan and queue complete. Downstream members are spawned by the Steward subagent in a Claude Code session.",
    )

    print(json.dumps({
        "status": "ok",
        "trace_id": trace_id,
        "scan": scan_result.get("returncode"),
        "queue_path": str(queue_path),
        "next": "Steward subagent (or manual Claude Code session) reads the queue",
    }, indent=2))
    return 0


def cmd_scan_only(args: argparse.Namespace) -> int:
    """Run the scan layer only and report. No queue, no Athena."""
    result = run_unified_scan(project=args.project, force=args.force, skip_kg=args.skip_kg)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def cmd_status(_args: argparse.Namespace) -> int:
    queue_exists = SENTINEL_QUEUE.exists()
    cache_exists = SCAN_CACHE.exists()
    queue_age_seconds = None
    if queue_exists:
        queue_age_seconds = int(datetime.now(timezone.utc).timestamp() - SENTINEL_QUEUE.stat().st_mtime)

    print("Sentinel — status")
    print("=" * 60)
    print(f"  Sentinel script:           {Path(__file__)}")
    print(f"  Wraps unified_scan.py:     {UNIFIED_SCAN.exists()}")
    print(f"  Athena available:          {_athena() is not None}")
    print(f"  Scan cache exists:         {cache_exists}")
    print(f"  Queue exists:              {queue_exists}")
    if queue_age_seconds is not None:
        print(f"  Queue age:                 {queue_age_seconds}s")
    print(f"  State dir:                 {SENTINEL_STATE_DIR}")
    return 0


def cmd_inspect_queue(_args: argparse.Namespace) -> int:
    if not SENTINEL_QUEUE.exists():
        print(json.dumps({"status": "no_queue"}))
        return 1
    print(SENTINEL_QUEUE.read_text())
    return 0


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Sentinel — entry point of every House of Prometheus cycle.",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_full = sub.add_parser("full", help="Run the full cycle: scan + queue.")
    p_full.add_argument("--project", default="anaro-labs", choices=["anaro-labs", "estate-mate"])
    p_full.add_argument("--force", action="store_true")
    p_full.add_argument("--skip-kg", action="store_true", default=True)
    p_full.set_defaults(func=cmd_full)

    p_scan = sub.add_parser("scan-only", help="Run the scan layer only.")
    p_scan.add_argument("--project", default="anaro-labs", choices=["anaro-labs", "estate-mate"])
    p_scan.add_argument("--force", action="store_true")
    p_scan.add_argument("--skip-kg", action="store_true", default=True)
    p_scan.set_defaults(func=cmd_scan_only)

    sub.add_parser("status", help="Print sentinel status.").set_defaults(func=cmd_status)
    sub.add_parser("inspect-queue", help="Print the current sentinel queue file.").set_defaults(func=cmd_inspect_queue)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Support legacy --full / --scan-only flags as a friendly alias
    args_in = list(argv) if argv is not None else sys.argv[1:]
    if args_in and args_in[0] in ("--full", "--scan-only", "--status"):
        # Translate legacy flags into the subcommand form
        legacy_to_sub = {"--full": "full", "--scan-only": "scan-only", "--status": "status"}
        args_in[0] = legacy_to_sub[args_in[0]]
    args = parser.parse_args(args_in)
    if not args.cmd:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
