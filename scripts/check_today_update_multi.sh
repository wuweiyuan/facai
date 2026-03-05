#!/usr/bin/env bash
set -euo pipefail

python3 scripts/check_data_freshness.py \
  --probe-symbol 000001 \
  --probe-symbol 600519 \
  --probe-symbol 300750 \
  --require any \
  --output table \
  "$@"
