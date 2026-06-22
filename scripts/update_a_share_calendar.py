#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable


DEFAULT_CALENDAR_PATH = Path(__file__).resolve().parents[1] / "data" / "a_share_closed_weekdays.csv"


def _date_key(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "date"):
        maybe_date = value.date()
        if isinstance(maybe_date, date):
            return maybe_date.isoformat()
    return str(value)[:10]


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "reason"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _closed_weekdays_for_year(
    year: int,
    trade_dates: Iterable[object],
    *,
    reason: str,
) -> list[dict[str, str]]:
    trade_date_keys = {_date_key(value) for value in trade_dates}
    if not any(key.startswith(f"{year}-") for key in trade_date_keys):
        raise ValueError(f"trade calendar does not contain any {year} trading dates")

    closed_weekdays: list[dict[str, str]] = []
    current = date(year, 1, 1)
    end = date(year, 12, 31)
    while current <= end:
        key = current.isoformat()
        if current.weekday() < 5 and key not in trade_date_keys:
            closed_weekdays.append({"date": key, "reason": reason})
        current += timedelta(days=1)
    return closed_weekdays


def update_closed_weekdays_cache(
    year: int,
    trade_dates: Iterable[object],
    calendar_path: str | Path = DEFAULT_CALENDAR_PATH,
    *,
    reason: str = "Exchange holiday",
) -> list[dict[str, str]]:
    path = Path(calendar_path)
    existing = _read_rows(path)
    updated_year_rows = _closed_weekdays_for_year(year, trade_dates, reason=reason)
    rows = [row for row in existing if not row["date"].startswith(f"{year}-")]
    rows.extend(updated_year_rows)
    rows.sort(key=lambda row: row["date"])
    _write_rows(path, rows)
    return updated_year_rows


def _fetch_trade_dates() -> list[object]:
    import akshare as ak

    return list(ak.tool_trade_date_hist_sina()["trade_date"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Update cached A-share closed weekdays for one year")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--calendar-path", default=str(DEFAULT_CALENDAR_PATH))
    parser.add_argument("--reason", default="Exchange holiday")
    args = parser.parse_args()

    updated = update_closed_weekdays_cache(
        args.year,
        _fetch_trade_dates(),
        args.calendar_path,
        reason=args.reason,
    )
    print(f"updated {len(updated)} closed weekdays for {args.year}: {args.calendar_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
