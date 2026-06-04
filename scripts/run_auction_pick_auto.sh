#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
AUCTION_COUNT="${AUCTION_COUNT:-5}"
REPORT_DIR="${PROJECT_ROOT}/reports/auction_pick"
RUN_DATE="$(date +%F)"
RUN_TIME="$(date '+%F %T')"
DAILY_LOG="${REPORT_DIR}/${RUN_DATE}.log"
LATEST_LOG="${REPORT_DIR}/latest.log"
TMP_LOG="${REPORT_DIR}/latest.tmp"

mkdir -p "${REPORT_DIR}"
cd "${PROJECT_ROOT}"

AUCTION_STATUS=0
{
  echo "=== auction-pick auto run ${RUN_TIME} ==="
  echo "project_root=${PROJECT_ROOT}"
  echo "python=${PYTHON_BIN}"
  echo "count=${AUCTION_COUNT}"
  set +e
  "${PYTHON_BIN}" -m app.main auction-pick --date "${RUN_DATE}" --count "${AUCTION_COUNT}" --output table
  AUCTION_STATUS=$?
  set -e
  if [[ "${AUCTION_STATUS}" -ne 0 ]]; then
    echo "=== auction-pick auto run failed status=${AUCTION_STATUS} $(date '+%F %T') ==="
  else
    echo "=== auction-pick auto run complete $(date '+%F %T') ==="
  fi
} > "${TMP_LOG}" 2>&1

cat "${TMP_LOG}" >> "${DAILY_LOG}"
cp "${TMP_LOG}" "${LATEST_LOG}"
rm -f "${TMP_LOG}"

if [[ "${AUCTION_STATUS}" -eq 0 ]]; then
  osascript -e 'display notification "竞价策略已完成，请查看 latest.log" with title "竞价选股"'
else
  osascript -e 'display notification "竞价策略运行失败，请查看 latest.log" with title "竞价选股"'
fi

open "${LATEST_LOG}"
exit "${AUCTION_STATUS}"
