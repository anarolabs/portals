#!/usr/bin/env bash
# ============================================================
# Scan and propose - runs scanner then proposer
# ============================================================
#
# Replaces direct unified_scan.py calls in launchd plists.
# Runs the scanner first (data collection), then the proposer
# (generates structured update proposals for the interactive skills).
#
# Usage:
#   scan_and_propose.sh --project all [--force]
#
# ============================================================

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
UV="/Users/romansiepelmeyer/.local/bin/uv"

# Step 1: Run the unified scanner
"$UV" run --with neo4j python3 "$SCRIPTS_DIR/unified_scan.py" "$@"

# Step 2: Generate proposals from scan results
python3 "$SCRIPTS_DIR/propose_updates.py" "$@"
