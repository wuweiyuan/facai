#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
AUCTION_COUNT="${AUCTION_COUNT:-2}"
LABEL="com.wayne.auction-pick"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "${HOME}/Library/LaunchAgents" "${PROJECT_ROOT}/reports/auction_pick"

"${PYTHON_BIN}" -m app.auction_pick.automation \
  --project-root "${PROJECT_ROOT}" \
  --output "${PLIST_PATH}" \
  --python-bin "${PYTHON_BIN}" \
  --hour 9 \
  --minute 26 \
  --count "${AUCTION_COUNT}"

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl load "${PLIST_PATH}"

echo "Installed ${LABEL}"
echo "Schedule: Monday-Friday 09:26"
echo "Plist: ${PLIST_PATH}"
echo "Latest log: ${PROJECT_ROOT}/reports/auction_pick/latest.log"
