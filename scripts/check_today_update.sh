#!/usr/bin/env bash
set -euo pipefail

python3 scripts/check_data_freshness.py --output table "$@"
