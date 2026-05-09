#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DAILY_DIR="${TOOL_ROOT}/reports/daily"

mkdir -p "${DAILY_DIR}"

find "${DAILY_DIR}" -type f -name 'module-resolver-*.log' -mtime +2 -delete
find "${DAILY_DIR}" -type f -name 'module-resolver-*.json' -mtime +2 -delete
find "${DAILY_DIR}" -type f -name 'module-resolver-*.html' -mtime +2 -delete
