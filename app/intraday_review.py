from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Protocol

from app.models import DailyBar


class IntradayReviewDataSource(Protocol):
    def get_trade_dates(self, start_date: date, end_date: date) -> list[date]:
        ...

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[DailyBar]:
        ...


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _pct_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"win_rate": 0.0, "avg": 0.0, "median": 0.0, "worst": 0.0}
    return {
        "win_rate": sum(1 for item in values if item > 0) / len(values),
        "avg": mean(values),
        "median": median(values),
        "worst": min(values),
    }


def _load_signal_rows(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def _next_trade_date(ds: IntradayReviewDataSource, trade_date: date) -> date | None:
    dates = ds.get_trade_dates(trade_date, trade_date + timedelta(days=10))
    for item in dates:
        if item > trade_date:
            return item
    return None


def _daily_bar_map(bars: list[DailyBar]) -> dict[date, DailyBar]:
    return {bar.trade_date: bar for bar in bars}


def analyze_intraday_pick_signals(
    path: str,
    ds: IntradayReviewDataSource,
    strategy: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    rows = []
    for row in _load_signal_rows(path):
        trade_date = _parse_date(str(row.get("trade_date", "")))
        if strategy and row.get("strategy") != strategy:
            continue
        if start_date and trade_date < start_date:
            continue
        if end_date and trade_date > end_date:
            continue
        item = dict(row)
        item["_trade_date_obj"] = trade_date
        rows.append(item)

    selected_rows = [row for row in rows if bool(row.get("selected"))]
    selected_dates = {row["_trade_date_obj"] for row in selected_rows}
    no_trade_dates = {
        row["_trade_date_obj"]
        for row in rows
        if not bool(row.get("selected")) and row["_trade_date_obj"] not in selected_dates
    }

    records: list[dict] = []
    skipped = 0
    for row in selected_rows:
        trade_date = row["_trade_date_obj"]
        symbol = str(row.get("symbol", ""))
        entry_price = row.get("entry_price")
        try:
            entry_price = float(entry_price)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not symbol or entry_price <= 0:
            skipped += 1
            continue
        exit_date = _next_trade_date(ds, trade_date)
        if exit_date is None:
            skipped += 1
            continue
        bars = ds.get_daily_bars(symbol, trade_date, exit_date)
        bar_map = _daily_bar_map(bars)
        exit_bar = bar_map.get(exit_date)
        if exit_bar is None or exit_bar.open <= 0 or exit_bar.close <= 0:
            skipped += 1
            continue
        ret_next_open = exit_bar.open / entry_price - 1.0
        ret_next_close = exit_bar.close / entry_price - 1.0
        records.append(
            {
                "strategy": row.get("strategy"),
                "trade_date": trade_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "rank": row.get("rank"),
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "entry_price": entry_price,
                "next_open": exit_bar.open,
                "next_close": exit_bar.close,
                "ret_next_open": ret_next_open,
                "ret_next_close": ret_next_close,
            }
        )

    next_open_returns = [row["ret_next_open"] for row in records]
    next_close_returns = [row["ret_next_close"] for row in records]
    next_open = _pct_summary(next_open_returns)
    next_close = _pct_summary(next_close_returns)
    strategies = sorted({str(row.get("strategy", "")) for row in rows if row.get("strategy")})
    return {
        "strategy": strategy or ",".join(strategies) or "all",
        "signal_days": len({row["_trade_date_obj"] for row in rows}),
        "no_trade_days": len(no_trade_dates),
        "selected_signals": len(selected_rows),
        "completed_trades": len(records),
        "skipped_signals": skipped,
        "win_rate_next_open": next_open["win_rate"],
        "avg_return_next_open": next_open["avg"],
        "median_return_next_open": next_open["median"],
        "worst_return_next_open": next_open["worst"],
        "win_rate_next_close": next_close["win_rate"],
        "avg_return_next_close": next_close["avg"],
        "records": records,
    }
