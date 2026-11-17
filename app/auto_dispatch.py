"""Start an automatic pick runner only on A-share trading days."""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from app.trading_calendar import is_a_share_trading_day


RUNNERS = {
    "auction": "run_auction_pick_auto.sh",
    "tail": "run_tail_pick_auto.sh",
}


def dispatch_pick(kind: str, run_date: date) -> int:
    if not is_a_share_trading_day(run_date, require_calendar_year=True).is_trading_day:
        return 0

    script = Path(__file__).resolve().parents[1] / "scripts" / RUNNERS[kind]
    os.execv("/bin/bash", ["/bin/bash", str(script)])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run automatic picks on A-share trading days only")
    parser.add_argument("kind", choices=RUNNERS)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    return dispatch_pick(args.kind, args.date)


if __name__ == "__main__":
    raise SystemExit(main())
