#!/usr/bin/env bash
set -euo pipefail

SERVICE_ID="${1:-com.foundryvtt.modulator}"
ROOT_DIR="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
DATA_ROOT="${3:-/Users/$(whoami)/foundry-data}"
PYTHON_BIN="${4:-$(command -v python3)}"
PORT="${5:-8787}"

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 not found in PATH"
  exit 1
fi

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${SERVICE_ID}.plist"
LOG_DIR="${ROOT_DIR}/logs"
mkdir -p "${PLIST_DIR}" "${LOG_DIR}"

cat > "${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${SERVICE_ID}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>backend.app.main:app</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>${PORT}</string>
  </array>
  <key>WorkingDirectory</key><string>${ROOT_DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>RESOLVER_DATA_ROOT</key><string>${DATA_ROOT}</string>
    <key>RESOLVER_BIND_HOST</key><string>0.0.0.0</string>
    <key>RESOLVER_BIND_PORT</key><string>${PORT}</string>
    <key>USE_NEW_UI</key><string>true</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG_DIR}/service.out.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/service.err.log</string>
</dict>
</plist>
EOF

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl load "${PLIST_PATH}"
echo "launchd service installed: ${SERVICE_ID}"
