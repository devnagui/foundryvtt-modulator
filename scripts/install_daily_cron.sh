#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_CMD="15 */6 * * * ${SCRIPT_DIR}/run_full_dry_run.sh"

CURRENT_CRONTAB="$(mktemp)"
UPDATED_CRONTAB="$(mktemp)"
trap 'rm -f "${CURRENT_CRONTAB}" "${UPDATED_CRONTAB}"' EXIT

crontab -l > "${CURRENT_CRONTAB}" 2>/dev/null || true
grep -Fv "${SCRIPT_DIR}/run_full_dry_run.sh" "${CURRENT_CRONTAB}" > "${UPDATED_CRONTAB}" || true
printf "%s\n" "${CRON_CMD}" >> "${UPDATED_CRONTAB}"
crontab "${UPDATED_CRONTAB}"
printf "Installed cron entry:\n%s\n" "${CRON_CMD}"
