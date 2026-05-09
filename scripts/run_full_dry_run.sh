#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPORTS_DIR="${TOOL_ROOT}/reports"
DAILY_DIR="${REPORTS_DIR}/daily"
PUBLIC_DIR="${REPORTS_DIR}/public"
DATE_STAMP="$(date -u +%F)"

mkdir -p "${DAILY_DIR}" "${PUBLIC_DIR}"

JSON_OUTPUT="${DAILY_DIR}/module-resolver-${DATE_STAMP}.json"
HTML_OUTPUT="${DAILY_DIR}/module-resolver-${DATE_STAMP}.html"
HTML_OUTPUT_V3="${DAILY_DIR}/module-resolver-${DATE_STAMP}-v3.html"
LOG_OUTPUT="${DAILY_DIR}/module-resolver-${DATE_STAMP}.log"

cd "${TOOL_ROOT}"
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --dry-run \
  --pretty \
  --batch-size 10 \
  --json-output "${JSON_OUTPUT}" \
  --html-report "${HTML_OUTPUT}" \
  --log-file "${LOG_OUTPUT}"

cp -f "${JSON_OUTPUT}" "${REPORTS_DIR}/module-resolver-latest.json"
cp -f "${HTML_OUTPUT}" "${REPORTS_DIR}/module-resolver-latest.html"
cp -f "${HTML_OUTPUT_V3}" "${REPORTS_DIR}/module-resolver-latest-v3.html"
cp -f "${LOG_OUTPUT}" "${REPORTS_DIR}/module-resolver-latest.log"
cp -f "${HTML_OUTPUT}" "${PUBLIC_DIR}/index.html"
cp -f "${JSON_OUTPUT}" "${PUBLIC_DIR}/module-resolver-latest.json"
mkdir -p "${PUBLIC_DIR}/v3"
cp -f "${HTML_OUTPUT_V3}" "${PUBLIC_DIR}/v3/index.html"

"${SCRIPT_DIR}/rotate_reports.sh"
