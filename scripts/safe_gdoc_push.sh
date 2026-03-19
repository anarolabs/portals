#!/usr/bin/env bash
# safe_gdoc_push.sh - Read-before-write Google Doc updater
#
# Forces a read-diff-push workflow:
# 1. Reads the current Google Doc as plain text
# 2. Compares to the last-pushed version (cached locally)
# 3. If external edits detected: shows the diff and EXITS (non-zero)
# 4. If clean: pushes the new markdown and caches the result
#
# Usage: safe_gdoc_push.sh DOC_ID FILE [PROJECT] [--force]
#
# --force: Push even when external edits are detected (use after reviewing diff)

set -euo pipefail

DOC_ID="${1:?Usage: safe_gdoc_push.sh DOC_ID FILE [PROJECT] [--force]}"
FILE="${2:?Usage: safe_gdoc_push.sh DOC_ID FILE [PROJECT] [--force]}"
PROJECT="${3:-anaro-labs}"
FORCE="${4:-}"

CACHE_DIR="/tmp/gdoc_cache"
mkdir -p "$CACHE_DIR"
LAST_PUSH_FILE="$CACHE_DIR/${DOC_ID}.txt"
CURRENT_FILE="$CACHE_DIR/${DOC_ID}.current.txt"

DRIVE_OPS="/Users/romansiepelmeyer/Documents/Claude Code/anaro-labs/00-meta/scripts/drive_operations.py"
PORTALS="/Users/romansiepelmeyer/Documents/Claude Code/portals/run.sh"

# Step 1: Read current Google Doc
python3 "$DRIVE_OPS" --read "$DOC_ID" 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('content',''))" \
  > "$CURRENT_FILE"

# Step 2: Diff against last push
if [ -f "$LAST_PUSH_FILE" ]; then
  if ! diff -u "$LAST_PUSH_FILE" "$CURRENT_FILE" > /dev/null 2>&1; then
    echo "=== EXTERNAL EDITS DETECTED ==="
    echo "The Google Doc has been modified since the last push."
    echo ""
    diff -u "$LAST_PUSH_FILE" "$CURRENT_FILE" || true
    echo ""
    echo "=== WARNING: --update-md will OVERWRITE these edits ==="

    if [[ "$FORCE" != "--force" ]]; then
      echo "Review the diff above. To push anyway: re-run with --force"
      exit 1
    else
      echo "Proceeding with --force."
    fi
  else
    echo "No external edits detected since last push."
  fi
else
  echo "First push for this document (no cached state). Current doc content:"
  echo "---"
  cat "$CURRENT_FILE"
  echo "---"
fi

# Step 3: Push
echo "Pushing to Google Doc..."
bash "$PORTALS" scripts/docs_operations.py --update-md "$DOC_ID" --file "$FILE" --project "$PROJECT"

# Step 4: Cache the new state for future diffs
python3 "$DRIVE_OPS" --read "$DOC_ID" 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('content',''))" \
  > "$LAST_PUSH_FILE"

echo "Push complete. State cached at $LAST_PUSH_FILE"
