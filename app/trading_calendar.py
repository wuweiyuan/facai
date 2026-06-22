from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


DEFAULT_CLOSED_WEEKDAYS_PATH = Path(__file__).resolve().parents[1] / "data" / "a_share_closed_weekdays.csv"


@dataclass(frozen=True)
class TradingDayCheck:
    is_trading_day: bool
    reason: str


def _load_closed_weekdays(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"A-share closed weekdays cache not found: {path}")

    with path.open(encoding="utf-8", newline="") as f:
        return {row["date"]: row["reason"] for row in csv.DictReader(f)}


def is_a_share_trading_day(
    run_date: str | date,
    *,
    closed_weekdays_path: str | Path = DEFAULT_CLOSED_WEEKDAYS_PATH,
) -> TradingDayCheck:
    if isinstance(run_date, date):
        checked_date = run_date
    else:
        checked_date = date.fromisoformat(run_date)

    if checked_date.weekday() >= 5:
        return TradingDayCheck(False, "weekend")

    closed_weekdays = _load_closed_weekdays(Path(closed_weekdays_path))
    reason = closed_weekdays.get(checked_date.isoformat())
    if reason:
        return TradingDayCheck(False, f"A-share market closed: {reason}")

    return TradingDayCheck(True, "A-share trading day")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a date is an A-share trading day")
    parser.add_argument("--date", required=True)
    parser.add_argument("--closed-weekdays", default=str(DEFAULT_CLOSED_WEEKDAYS_PATH))
    args = parser.parse_args()

    result = is_a_share_trading_day(args.date, closed_weekdays_path=args.closed_weekdays)
    print(result.reason)
    return 0 if result.is_trading_day else 2


if __name__ == "__main__":
    raise SystemExit(main())
