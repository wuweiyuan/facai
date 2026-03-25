from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean

import pandas as pd

from app.config import apply_strategy_profile
from app.features.indicators import add_indicators
from app.models import BacktestRecord, StockInfo
from app.sector_strength import build_sector_metrics_from_cache, load_symbol_sector_map, should_use_sector_metrics
from app.strategy.regime_risk import detect_market_state, passes_risk_filter
from app.strategy.scoring import compute_score, passes_threshold
from app.universe.filtering import filter_universe


def run_local_single_backtest(base_cfg: dict, profile_name: str | None, start_date: date, end_date: date, count: int | None = None) -> dict:
    cfg = apply_strategy_profile(base_cfg, profile_name)
    cfg.setdefault("data_freshness", {})["enabled"] = False
    cache_dir = Path(str(cfg.get("data_source", {}).get("cache_dir", ".cache/akshare")))
    bars_dir = cache_dir / "bars"
    meta_dir = cache_dir / "meta"
    index_dir = cache_dir / "index"
    index_symbol = str(cfg.get("market_filter", {}).get("index_symbol", "000300"))
    if not bars_dir.exists():
        raise RuntimeError("Local bars cache directory not found")

    stocks: list[StockInfo] = []
    with (meta_dir / "stock_list.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            stocks.append(
                StockInfo(
                    symbol=str(row["symbol"]).zfill(6),
                    name=row.get("name", ""),
                    is_st=str(row.get("is_st", "")).lower() == "true",
                    is_paused=str(row.get("is_paused", "")).lower() == "true",
                    market=row.get("market") or None,
                )
            )

    trade_dates: list[date] = []
    with (meta_dir / "trade_calendar.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            dt = datetime.strptime(row["trade_date"], "%Y-%m-%d").date()
            if start_date <= dt <= end_date:
                trade_dates.append(dt)
    trade_dates.sort()
    if len(trade_dates) < 8:
        raise RuntimeError("Not enough trade dates for backtest")
    attempted_dates = trade_dates[:-5]
    attempted_set = set(attempted_dates)

    index_closes = {}
    with (index_dir / f"{index_symbol}.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            index_closes[datetime.strptime(row["trade_date"], "%Y-%m-%d").date()] = float(row["close"])
    market_states = {dt: detect_market_state(index_closes, dt, cfg) for dt in attempted_dates}
    sector_symbol_map = load_symbol_sector_map(cfg) if should_use_sector_metrics(cfg) else {}
    sector_metrics = (
        build_sector_metrics_from_cache(cache_dir, sector_symbol_map, start_date, end_date)
        if sector_symbol_map and should_use_sector_metrics(cfg)
        else {}
    )

    available_symbols = {p.stem for p in bars_dir.glob("*.csv")}
    universe = {item.symbol for item in filter_universe(stocks, cfg, start_date) if item.symbol in available_symbols}
    pick_count = max(int(count if count is not None else cfg.get("strategy", {}).get("pick_count", 1)), 1)

    candidates: dict[date, list[tuple[str, str, float, float | None, float | None, float | None, str]]] = defaultdict(list)
    for path in bars_dir.glob("*.csv"):
        symbol = path.stem
        if symbol not in universe:
            continue
        df = pd.read_csv(path)
        if df.empty or len(df) < 70:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
        if df.empty:
            continue
        df = add_indicators(df)
        df["ret_fwd_1"] = df["close"].shift(-1) / df["close"] - 1.0
        df["ret_fwd_3"] = df["close"].shift(-3) / df["close"] - 1.0
        df["ret_fwd_5"] = df["close"].shift(-5) / df["close"] - 1.0
        df = df[df["trade_date"].isin(attempted_set)]
        if df.empty:
            continue

        for row in df.itertuples(index=False):
            latest_dict = row._asdict()
            signal_date = latest_dict["trade_date"]
            latest_view = dict(latest_dict)
            latest_view["market_mom20"] = market_states[signal_date].mom20
            sector = sector_symbol_map.get(symbol)
            if sector:
                metrics = sector_metrics.get((signal_date, sector))
                if metrics:
                    latest_view["sector_mom20"] = metrics.get("sector_mom20", 0.0)
                    latest_view["sector_mom5"] = metrics.get("sector_mom5", 0.0)
                    latest_view["mom20_excess_vs_sector"] = float(latest_view.get("mom20", 0.0)) - float(latest_view["sector_mom20"])
            if not passes_threshold(latest_view, "normal", cfg):
                continue
            if not passes_risk_filter(latest_view, market_states[signal_date], "normal", cfg):
                continue
            score_total, _ = compute_score(latest_view, cfg)
            candidates[signal_date].append(
                (
                    symbol,
                    symbol,
                    score_total,
                    _apply_round_trip_cost(latest_dict.get("ret_fwd_1"), cfg),
                    _apply_round_trip_cost(latest_dict.get("ret_fwd_3"), cfg),
                    _apply_round_trip_cost(latest_dict.get("ret_fwd_5"), cfg),
                    "normal",
                )
            )

    records: list[BacktestRecord] = []
    mode_counts: dict[str, int] = {"normal": 0}
    for dt in attempted_dates:
        day = sorted(candidates.get(dt, []), key=lambda item: item[2], reverse=True)[:pick_count]
        if not day:
            continue
        mode_counts["normal"] += 1
        records.append(
            BacktestRecord(
                trade_date=dt,
                symbol="+".join(item[0] for item in day),
                name="+".join(item[1] for item in day),
                threshold_mode="normal",
                ret_1d_gross=None,
                ret_3d_gross=None,
                ret_5d_gross=None,
                ret_1d_net=_mean([item[3] for item in day]),
                ret_3d_net=_mean([item[4] for item in day]),
                ret_5d_net=_mean([item[5] for item in day]),
            )
        )

    one_net = [r.ret_1d_net for r in records if r.ret_1d_net is not None]
    three_net = [r.ret_3d_net for r in records if r.ret_3d_net is not None]
    five_net = [r.ret_5d_net for r in records if r.ret_5d_net is not None]
    equity = 1.0
    curve = []
    for value in one_net:
        equity *= 1 + value
        curve.append(equity)
    peak = 1.0
    max_dd = 0.0
    for value in curve:
        peak = max(peak, value)
        dd = (peak - value) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return {
        "period": f"{start_date.isoformat()} -> {end_date.isoformat()}",
        "attempted_days": len(attempted_dates),
        "total_trades": len(records),
        "skipped_days": max(len(attempted_dates) - len(records), 0),
        "win_rate_gross_1d": 0.0,
        "win_rate_gross_3d": 0.0,
        "win_rate_net_1d": _win_rate(one_net),
        "win_rate_net_3d": _win_rate(three_net),
        "avg_return_1d_gross": 0.0,
        "avg_return_3d_gross": 0.0,
        "avg_return_5d_gross": 0.0,
        "avg_return_1d_net": _mean(one_net) or 0.0,
        "avg_return_3d_net": _mean(three_net) or 0.0,
        "avg_return_5d_net": _mean(five_net) or 0.0,
        "max_drawdown_proxy": max_dd,
        "threshold_mode_counts": mode_counts,
        "error_counts": {},
        "error_examples": [],
        "records": [asdict(record) for record in records],
    }


def _apply_round_trip_cost(gross_ret: float | None, cfg: dict) -> float | None:
    if gross_ret is None or pd.isna(gross_ret):
        return None
    ecfg = cfg.get("execution_cost", {})
    if not bool(ecfg.get("enabled", True)):
        return float(gross_ret)
    slip = float(ecfg.get("slippage_bps", 5.0)) / 10000.0
    comm = float(ecfg.get("commission_rate", 0.0002))
    stamp = float(ecfg.get("stamp_duty_sell_rate", 0.0005))
    min_commission = float(ecfg.get("min_commission_per_side", 0.0))
    buy_fee = max(comm, min_commission)
    sell_fee = max(comm + stamp, min_commission)
    gross_factor = 1.0 + float(gross_ret)
    if gross_factor <= 0:
        return -1.0
    return gross_factor * (1.0 - slip) * (1.0 - sell_fee) / ((1.0 + slip) * (1.0 + buy_fee)) - 1.0


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
