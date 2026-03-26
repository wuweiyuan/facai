from __future__ import annotations

from datetime import date

import pandas as pd

from app.models import DailyBar

ENTRY_PRICE_CLOSE = "close"
ENTRY_PRICE_NEXT_OPEN = "next-open"
VALID_ENTRY_PRICE_MODES = {ENTRY_PRICE_CLOSE, ENTRY_PRICE_NEXT_OPEN}


def normalize_entry_price_mode(mode: str | None) -> str:
    normalized = str(mode or ENTRY_PRICE_CLOSE).strip().lower()
    if normalized not in VALID_ENTRY_PRICE_MODES:
        raise ValueError(f"Unsupported entry price mode: {mode}")
    return normalized


def entry_price_mode_label(mode: str) -> str:
    normalized = normalize_entry_price_mode(mode)
    labels = {
        ENTRY_PRICE_CLOSE: "signal_close",
        ENTRY_PRICE_NEXT_OPEN: "next_open",
    }
    return labels[normalized]


def entry_price_mode_description(mode: str) -> str:
    normalized = normalize_entry_price_mode(mode)
    descriptions = {
        ENTRY_PRICE_CLOSE: "信号日收盘买入",
        ENTRY_PRICE_NEXT_OPEN: "信号次日开盘买入",
    }
    return descriptions[normalized]


def signal_attempted_dates(trade_dates: list[date], mode: str) -> list[date]:
    normalized = normalize_entry_price_mode(mode)
    reserve_days = 5 if normalized == ENTRY_PRICE_CLOSE else 6
    if len(trade_dates) <= reserve_days:
        return []
    return trade_dates[:-reserve_days]


def add_signal_forward_returns(frame: pd.DataFrame, mode: str) -> pd.DataFrame:
    normalized = normalize_entry_price_mode(mode)
    out = frame.copy()
    if normalized == ENTRY_PRICE_CLOSE:
        entry_prices = out["close"]
        exit_steps = {1: 1, 3: 3, 5: 5}
    else:
        entry_prices = out["open"].shift(-1)
        exit_steps = {1: 2, 3: 4, 5: 6}
    for hold_days, exit_step in exit_steps.items():
        out[f"ret_fwd_{hold_days}"] = out["close"].shift(-exit_step) / entry_prices - 1.0
    return out


def calc_target_forward_return(
    bar_map: dict[date, DailyBar],
    target_date: date,
    trade_dates: list[date],
    step: int,
    mode: str,
) -> float | None:
    normalized = normalize_entry_price_mode(mode)
    if target_date not in trade_dates:
        return None
    idx = trade_dates.index(target_date)
    if idx + step >= len(trade_dates):
        return None
    entry_bar = bar_map.get(target_date)
    exit_bar = bar_map.get(trade_dates[idx + step])
    if entry_bar is None or exit_bar is None:
        return None
    entry_price = entry_bar.close if normalized == ENTRY_PRICE_CLOSE else entry_bar.open
    exit_price = exit_bar.close
    if entry_price is None or exit_price is None or entry_price <= 0:
        return None
    return exit_price / entry_price - 1.0
