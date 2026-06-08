from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd

from app.backtest.local_single import _apply_round_trip_cost
from app.features.indicators import add_indicators
from app.models import BacktestRecord, StockInfo
from app.universe.filtering import filter_universe


def run_local_intraday_proxy_backtest(
    base_cfg: dict,
    strategy: str,
    start_date: date,
    end_date: date,
    count: int | None = None,
) -> dict:
    if strategy not in {"auction-pick", "tail-pick"}:
        raise ValueError(f"Unsupported intraday proxy strategy: {strategy}")
    cache = _LocalProxyCache(str(base_cfg.get("data_source", {}).get("cache_dir", ".cache/akshare")))
    if not cache.is_available():
        raise RuntimeError("Local bars cache is unavailable")

    stocks = cache.load_stock_list()
    stock_names = {item.symbol: item.name or item.symbol for item in stocks}
    trade_dates = [dt for dt in cache.load_trade_dates() if start_date <= dt <= end_date]
    if len(trade_dates) < 2:
        raise RuntimeError("Not enough trade dates for proxy backtest")
    attempted_dates = trade_dates[:-1]
    attempted_set = set(attempted_dates)
    available_symbols = {path.stem for path in cache.iter_bar_files()}
    universe = {item.symbol for item in filter_universe(stocks, base_cfg, start_date) if item.symbol in available_symbols}
    filters = _filters(base_cfg, strategy)
    pick_count = max(int(count if count is not None else filters.get("count", 1)), 1)

    candidates: dict[date, list[_ProxyCandidate]] = defaultdict(list)
    for path in cache.iter_bar_files():
        symbol = path.stem
        if symbol not in universe:
            continue
        df = pd.read_csv(path)
        if df.empty or len(df) < 70:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[df["trade_date"] <= end_date].copy()
        if df.empty:
            continue
        df = add_indicators(df)
        df["prev_close"] = df["close"].shift(1)
        df["next_open"] = df["open"].shift(-1)
        df = df[df["trade_date"].isin(attempted_set)]
        for row in df.itertuples(index=False):
            item = row._asdict()
            trade_date = item["trade_date"]
            candidate = _score_proxy_candidate(symbol, stock_names.get(symbol, symbol), item, strategy, filters, base_cfg)
            if candidate is not None:
                candidates[trade_date].append(candidate)

    records: list[BacktestRecord] = []
    for trade_date in attempted_dates:
        day = sorted(candidates.get(trade_date, []), key=lambda item: (-item.score, item.symbol))[:pick_count]
        if not day:
            continue
        records.append(
            BacktestRecord(
                trade_date=trade_date,
                symbol="+".join(item.symbol for item in day),
                name="+".join(item.name for item in day),
                threshold_mode="proxy",
                ret_1d_gross=_mean([item.ret_gross for item in day]),
                ret_3d_gross=None,
                ret_5d_gross=None,
                ret_1d_net=_mean([item.ret_net for item in day]),
                ret_3d_net=None,
                ret_5d_net=None,
            )
        )

    return _summary(strategy, records, start_date, end_date, len(attempted_dates))


class _LocalProxyCache:
    def __init__(self, cache_dir: str) -> None:
        self.root = Path(cache_dir)
        self.bars_dir = self.root / "bars"
        self.meta_dir = self.root / "meta"

    def is_available(self) -> bool:
        return (
            self.bars_dir.exists()
            and (self.meta_dir / "trade_calendar.csv").exists()
            and (self.meta_dir / "stock_list.csv").exists()
        )

    def load_stock_list(self) -> list[StockInfo]:
        items: list[StockInfo] = []
        with (self.meta_dir / "stock_list.csv").open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                items.append(
                    StockInfo(
                        symbol=str(row["symbol"]).zfill(6),
                        name=row.get("name", ""),
                        is_st=str(row.get("is_st", "")).lower() == "true",
                        is_paused=str(row.get("is_paused", "")).lower() == "true",
                        market=row.get("market") or None,
                    )
                )
        return items

    def load_trade_dates(self) -> list[date]:
        out: list[date] = []
        with (self.meta_dir / "trade_calendar.csv").open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out.append(datetime.strptime(row["trade_date"], "%Y-%m-%d").date())
        out.sort()
        return out

    def iter_bar_files(self):
        yield from self.bars_dir.glob("*.csv")


class _ProxyCandidate:
    def __init__(self, symbol: str, name: str, score: float, ret_gross: float, ret_net: float | None) -> None:
        self.symbol = symbol
        self.name = name
        self.score = score
        self.ret_gross = ret_gross
        self.ret_net = ret_net


def _filters(cfg: dict, strategy: str) -> dict[str, float]:
    if strategy == "auction-pick":
        raw = cfg.get("auction_pick", {}) if isinstance(cfg.get("auction_pick", {}), dict) else {}
        return {
            "count": float(raw.get("count", 2)),
            "min_opening_gap": float(raw.get("min_opening_gap", 0.012)),
            "max_opening_gap": float(raw.get("max_opening_gap", 0.04)),
            "min_current_return": float(raw.get("min_current_return", raw.get("min_opening_gap", 0.012))),
            "max_current_return": float(raw.get("max_current_return", 0.055)),
            "max_close_above_ma20_pct": float(raw.get("max_close_above_ma20_pct", 0.08)),
            "max_rsi14": float(raw.get("max_rsi14", 75)),
            "min_ma20_slope5": float(raw.get("min_ma20_slope5", 0.0)),
        }
    raw = cfg.get("tail_pick", {}) if isinstance(cfg.get("tail_pick", {}), dict) else {}
    return {
        "count": 1.0,
        "min_intraday_return": float(raw.get("min_intraday_return", 0.01)),
        "max_intraday_return": float(raw.get("max_intraday_return", 0.06)),
        "min_latest_vs_open": float(raw.get("min_latest_vs_open", 1.0)),
        "min_close_position": float(raw.get("min_close_position", 0.65)),
        "max_fade_from_high": float(raw.get("max_fade_from_high", 0.025)),
        "max_close_above_ma20_pct": float(raw.get("max_close_above_ma20_pct", 0.10)),
        "max_rsi14": float(raw.get("max_rsi14", 78)),
        "min_ma20_slope5": float(raw.get("min_ma20_slope5", 0.0)),
    }


def _score_proxy_candidate(
    symbol: str,
    name: str,
    row: dict[str, Any],
    strategy: str,
    filters: dict[str, float],
    cfg: dict,
) -> _ProxyCandidate | None:
    prev_close = _to_float(row.get("prev_close"))
    open_price = _to_float(row.get("open"))
    high = _to_float(row.get("high"))
    low = _to_float(row.get("low"))
    close = _to_float(row.get("close"))
    next_open = _to_float(row.get("next_open"))
    ma20 = _to_float(row.get("ma20"))
    ma60 = _to_float(row.get("ma60"))
    rsi14 = _to_float(row.get("rsi14"))
    ma20_slope5 = _to_float(row.get("ma20_slope5"))
    if None in {prev_close, open_price, high, low, close, next_open, ma20, ma60}:
        return None
    if min(prev_close, open_price, high, low, close, next_open, ma20, ma60) <= 0:
        return None
    distance_above_ma20 = close / ma20 - 1.0
    if close < ma20 or ma20 < ma60:
        return None
    if distance_above_ma20 > filters["max_close_above_ma20_pct"]:
        return None
    if rsi14 is not None and rsi14 == rsi14 and rsi14 > filters["max_rsi14"]:
        return None
    if ma20_slope5 is None or ma20_slope5 != ma20_slope5 or ma20_slope5 < filters["min_ma20_slope5"]:
        return None

    if strategy == "auction-pick":
        gap = open_price / prev_close - 1.0
        current_return = gap
        if gap < filters["min_opening_gap"] or gap > filters["max_opening_gap"]:
            return None
        if current_return < filters["min_current_return"] or current_return > filters["max_current_return"]:
            return None
        entry_price = open_price
        score = _center_score(gap, filters["min_opening_gap"], filters["max_opening_gap"]) * 50.0
        score += min(max(ma20_slope5 or 0.0, 0.0) / 0.03, 1.0) * 30.0
        score += min(max(distance_above_ma20, 0.0), 0.08) / 0.08 * 20.0
    else:
        intraday_return = close / prev_close - 1.0
        if intraday_return < filters["min_intraday_return"] or intraday_return > filters["max_intraday_return"]:
            return None
        if close < open_price * filters["min_latest_vs_open"]:
            return None
        close_position = (close - low) / (high - low) if high > low else 0.0
        if close_position < filters["min_close_position"]:
            return None
        if high > 0 and close / high - 1.0 < -filters["max_fade_from_high"]:
            return None
        entry_price = close
        score = _center_score(intraday_return, filters["min_intraday_return"], filters["max_intraday_return"]) * 45.0
        score += close_position * 35.0
        score += min(max(distance_above_ma20, 0.0), 0.10) / 0.10 * 20.0

    gross_ret = next_open / entry_price - 1.0
    return _ProxyCandidate(
        symbol=symbol,
        name=name,
        score=score,
        ret_gross=gross_ret,
        ret_net=_apply_round_trip_cost(gross_ret, cfg),
    )


def _center_score(value: float, low: float, high: float) -> float:
    mid = (low + high) / 2.0
    half_width = max((high - low) / 2.0, 0.001)
    return max(1.0 - abs(value - mid) / half_width, 0.0)


def _summary(strategy: str, records: list[BacktestRecord], start_date: date, end_date: date, attempted_days: int) -> dict:
    one_gross = [r.ret_1d_gross for r in records if r.ret_1d_gross is not None]
    one_net = [r.ret_1d_net for r in records if r.ret_1d_net is not None]
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in one_net:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
    entry_desc = (
        "日线代理: 信号日开盘买入，次交易日开盘卖出"
        if strategy == "auction-pick"
        else "日线代理: 信号日收盘买入，次交易日开盘卖出"
    )
    return {
        "strategy": strategy,
        "proxy_note": "日线代理回测，不是历史盘中快照重放。",
        "period": f"{start_date.isoformat()} -> {end_date.isoformat()}",
        "entry_price_mode": "daily-proxy-next-open-exit",
        "entry_price_desc": entry_desc,
        "attempted_days": attempted_days,
        "total_trades": len(records),
        "skipped_days": max(attempted_days - len(records), 0),
        "win_rate_gross_1d": _win_rate(one_gross),
        "win_rate_gross_3d": 0.0,
        "win_rate_net_1d": _win_rate(one_net),
        "win_rate_net_3d": 0.0,
        "avg_return_1d_gross": _mean(one_gross) or 0.0,
        "avg_return_3d_gross": 0.0,
        "avg_return_5d_gross": 0.0,
        "avg_return_1d_net": _mean(one_net) or 0.0,
        "avg_return_3d_net": 0.0,
        "avg_return_5d_net": 0.0,
        "max_drawdown_proxy": max_dd,
        "threshold_mode_counts": dict(Counter({"proxy": len(records)})),
        "error_counts": {},
        "error_examples": [],
        "records": [asdict(record) for record in records],
    }


def _to_float(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(out):
        return None
    return out


def _mean(values: list[float | None]) -> float | None:
    nums = [value for value in values if value is not None]
    if not nums:
        return None
    return mean(nums)


def _win_rate(values: list[float | None]) -> float:
    nums = [value for value in values if value is not None]
    if not nums:
        return 0.0
    return sum(1 for value in nums if value > 0) / len(nums)
