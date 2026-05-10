#!/usr/bin/env bash
set -euo pipefail

SERVICE_ID="${1:-com.foundryvtt.modulator}"
PLIST_PATH="$HOME/Library/LaunchAgents/${SERVICE_ID}.plist"

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
rm -f "${PLIST_PATH}"
echo "launchd service removed: ${SERVICE_ID}"
