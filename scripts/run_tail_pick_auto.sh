#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REPORT_DIR="${PROJECT_ROOT}/reports/tail_pick"
RUN_DATE="$(date +%F)"
RUN_TIME="$(date '+%F %T')"
DAILY_LOG="${REPORT_DIR}/${RUN_DATE}.log"
LATEST_LOG="${REPORT_DIR}/latest.log"
TMP_LOG="${REPORT_DIR}/latest.tmp"

mkdir -p "${REPORT_DIR}"
cd "${PROJECT_ROOT}"

TAIL_STATUS=0
SKIP_RUN=0
{
  echo "=== tail-pick auto run ${RUN_TIME} ==="
  echo "project_root=${PROJECT_ROOT}"
  echo "python=${PYTHON_BIN}"
  set +e
  TRADING_DAY_REASON="$("${PYTHON_BIN}" -m app.trading_calendar --date "${RUN_DATE}")"
  TRADING_DAY_STATUS=$?
  set -e
  echo "trading_day_check=${TRADING_DAY_REASON}"
  if [[ "${TRADING_DAY_STATUS}" -eq 2 ]]; then
    echo "not an A-share trading day; skip tail-pick"
    SKIP_RUN=1
  elif [[ "${TRADING_DAY_STATUS}" -ne 0 ]]; then
    echo "trading day check failed status=${TRADING_DAY_STATUS}"
    TAIL_STATUS="${TRADING_DAY_STATUS}"
  fi

  if [[ "${SKIP_RUN}" -eq 0 && "${TAIL_STATUS}" -eq 0 ]]; then
    set +e
    "${PYTHON_BIN}" -m app.main tail-pick --date "${RUN_DATE}" --output table
    TAIL_STATUS=$?
    set -e
  fi
  if [[ "${TAIL_STATUS}" -ne 0 ]]; then
    echo "=== tail-pick auto run failed status=${TAIL_STATUS} $(date '+%F %T') ==="
  elif [[ "${SKIP_RUN}" -eq 1 ]]; then
    echo "=== tail-pick auto run skipped $(date '+%F %T') ==="
  else
    echo "=== tail-pick auto run complete $(date '+%F %T') ==="
  fi
} > "${TMP_LOG}" 2>&1

cat "${TMP_LOG}" >> "${DAILY_LOG}"
cp "${TMP_LOG}" "${LATEST_LOG}"
rm -f "${TMP_LOG}"

if [[ "${SKIP_RUN}" -eq 1 ]]; then
  exit 0
fi

if [[ "${TAIL_STATUS}" -eq 0 ]]; then
  osascript -e 'display notification "尾盘策略已完成，请查看 latest.log" with title "尾盘选股"'
else
  osascript -e 'display notification "尾盘策略运行失败，请查看 latest.log" with title "尾盘选股"'
fi

open "${LATEST_LOG}"
exit "${TAIL_STATUS}"
