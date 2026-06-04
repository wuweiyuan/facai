#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LABEL="com.wayne.tail-pick"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "${HOME}/Library/LaunchAgents" "${PROJECT_ROOT}/reports/tail_pick"

"${PYTHON_BIN}" -m app.tail_pick.automation \
  --project-root "${PROJECT_ROOT}" \
  --output "${PLIST_PATH}" \
  --python-bin "${PYTHON_BIN}" \
  --hour 14 \
  --minute 44

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl load "${PLIST_PATH}"

echo "Installed ${LABEL}"
echo "Schedule: Monday-Friday 14:44"
echo "Plist: ${PLIST_PATH}"
echo "Latest log: ${PROJECT_ROOT}/reports/tail_pick/latest.log"
