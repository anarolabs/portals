#!/usr/bin/env bash
set -euo pipefail
PORTALS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PORTALS_DIR"
exec uv run python "$@"
