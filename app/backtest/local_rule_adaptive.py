from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean

import pandas as pd

from app.config import apply_strategy_profile
from app.features.indicators import add_indicators
from app.models import BacktestRecord, StockInfo
from app.strategy.regime_risk import detect_market_state, passes_risk_filter
from app.strategy.scoring import compute_score, passes_threshold
from app.universe.filtering import filter_universe


def run_local_rule_adaptive_backtest(base_cfg: dict, start_date: date, end_date: date, count_override: int | None = None) -> dict:
    cache_dir = Path(str(base_cfg.get("data_source", {}).get("cache_dir", ".cache/akshare")))
    bars_dir = cache_dir / "bars"
    meta_dir = cache_dir / "meta"
    index_dir = cache_dir / "index"
    index_symbol = str(base_cfg.get("market_filter", {}).get("index_symbol", "000300"))

    profiles = {
        "recommend": json.loads(json.dumps(base_cfg)),
        "recommend-pullback": apply_strategy_profile(base_cfg, "pullback_confirm"),
        "recommend-oversold": apply_strategy_profile(base_cfg, "oversold_rebound"),
    }
    for cfg in profiles.values():
        cfg.setdefault("data_freshness", {})["enabled"] = False

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
    market_labels = {dt: detect_market_state(index_closes, dt, base_cfg).label for dt in attempted_dates}
    market_states_by_profile = {
        name: {dt: detect_market_state(index_closes, dt, cfg) for dt in attempted_dates}
        for name, cfg in profiles.items()
    }

    available_symbols = {p.stem for p in bars_dir.glob("*.csv")}
    universe_by_profile: dict[str, set[str]] = {}
    for name, cfg in profiles.items():
        universe_by_profile[name] = {
            item.symbol
            for item in filter_universe(stocks, cfg, start_date)
            if item.symbol in available_symbols
        }

    order_map = base_cfg.get("adaptive_strategy", {}).get("regime_orders", {})
    pick_count_map = base_cfg.get("adaptive_strategy", {}).get("strategy_pick_counts", {})

    candidates: dict[str, dict[date, list[dict]]] = {name: defaultdict(list) for name in profiles}
    bars_cache: dict[str, pd.DataFrame] = {}

    for path in bars_dir.glob("*.csv"):
        symbol = path.stem
        if not any(symbol in items for items in universe_by_profile.values()):
            continue
        df = pd.read_csv(path)
        if df.empty or len(df) < 70:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
        if df.empty:
            continue
        df = add_indicators(df)
        bars_cache[symbol] = df
        df = df[df["trade_date"].isin(attempted_set)]
        if df.empty:
            continue
        for row in df.itertuples(index=False):
            latest_dict = row._asdict()
            latest = pd.Series(latest_dict)
            signal_date = latest_dict["trade_date"]
            for name, cfg in profiles.items():
                if symbol not in universe_by_profile[name]:
                    continue
                latest_copy = latest.copy()
                latest_copy["market_mom20"] = market_states_by_profile[name][signal_date].mom20
                if not passes_threshold(latest_copy, "normal", cfg):
                    continue
                if not passes_risk_filter(latest_copy, market_states_by_profile[name][signal_date], "normal", cfg):
                    continue
                score_total, _ = compute_score(latest_copy, cfg)
                candidates[name][signal_date].append(
                    {
                        "symbol": symbol,
                        "score": score_total,
                        "close": float(latest_dict["close"]),
                    }
                )

    records: list[BacktestRecord] = []
    strategy_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    for dt in attempted_dates:
        regime = market_labels[dt]
        ordered = order_map.get(regime) or order_map.get("unknown") or ["recommend-pullback"]
        chosen_cmd: str | None = None
        picked: list[dict] = []
        for cmd_name in ordered:
            day = sorted(candidates[cmd_name].get(dt, []), key=lambda item: item["score"], reverse=True)
            if not day:
                continue
            chosen_cmd = cmd_name
            pick_count = count_override if count_override is not None else pick_count_map.get(cmd_name, 1)
            pick_count = max(int(pick_count), 1)
            picked = day[:pick_count]
            break
        if not picked or not chosen_cmd:
            continue

        trade_dates_for_eval = [item["trade_date"] for item in bars_cache[picked[0]["symbol"]]["trade_date"].to_frame().itertuples(index=False) if item]
        basket_1d = []
        basket_3d = []
        basket_5d = []
        for item in picked:
            symbol = item["symbol"]
            rule_ret = _simulate_rule_exit(symbol, dt, trade_dates, bars_cache[symbol], chosen_cmd)
            basket_1d.append(rule_ret["ret_1d_net"])
            basket_3d.append(rule_ret["ret_3d_net"])
            basket_5d.append(rule_ret["ret_5d_net"])
        strategy_counts[chosen_cmd] += 1
        mode_counts["normal"] += 1
        records.append(
            BacktestRecord(
                trade_date=dt,
                symbol=chosen_cmd,
                name=chosen_cmd,
                threshold_mode="normal",
                ret_1d_gross=None,
                ret_3d_gross=None,
                ret_5d_gross=None,
                ret_1d_net=_mean(basket_1d),
                ret_3d_net=_mean(basket_3d),
                ret_5d_net=_mean(basket_5d),
            )
        )

    return _build_summary(records, start_date, end_date, len(attempted_dates), dict(strategy_counts), dict(mode_counts))


def _simulate_rule_exit(symbol: str, signal_date: date, trade_dates: list[date], bars_df: pd.DataFrame, strategy_name: str) -> dict[str, float | None]:
    if signal_date not in trade_dates:
        return {"ret_1d_net": None, "ret_3d_net": None, "ret_5d_net": None}
    idx = trade_dates.index(signal_date)
    path = bars_df[bars_df["trade_date"] >= signal_date].copy().reset_index(drop=True)
    if len(path) < 2:
        return {"ret_1d_net": None, "ret_3d_net": None, "ret_5d_net": None}
    entry = float(path.iloc[0]["close"])
    one_day = float(path.iloc[1]["close"]) / entry - 1.0 if len(path) > 1 else None

    if strategy_name == "recommend-oversold":
        max_hold = min(3, len(path) - 1)
        take_profit = 0.06
        stop_loss = -0.055
        idle_day_limit = 2
    elif strategy_name == "recommend-pullback":
        max_hold = min(4, len(path) - 1)
        take_profit = 0.07
        stop_loss = -0.04
        idle_day_limit = 3
    else:
        max_hold = min(3, len(path) - 1)
        take_profit = 0.06
        stop_loss = -0.045
        idle_day_limit = 2

    exit_ret = None
    best_seen = -1.0
    for day_idx in range(1, max_hold + 1):
        ret = float(path.iloc[day_idx]["close"]) / entry - 1.0
        best_seen = max(best_seen, ret)
        if ret <= stop_loss:
            exit_ret = ret
            break
        if ret >= take_profit:
            exit_ret = ret
            break
        if day_idx >= idle_day_limit and best_seen < 0.02:
            exit_ret = ret
            break
    if exit_ret is None:
        exit_ret = float(path.iloc[max_hold]["close"]) / entry - 1.0

    three_day = exit_ret if max_hold >= 3 else exit_ret
    five_day = exit_ret
    return {
        "ret_1d_net": _apply_round_trip_cost(one_day),
        "ret_3d_net": _apply_round_trip_cost(three_day),
        "ret_5d_net": _apply_round_trip_cost(five_day),
    }


def _build_summary(
    records: list[BacktestRecord],
    start_date: date,
    end_date: date,
    attempted_days: int,
    strategy_counts: dict[str, int],
    mode_counts: dict[str, int],
) -> dict:
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
        "attempted_days": attempted_days,
        "total_trades": len(records),
        "skipped_days": max(attempted_days - len(records), 0),
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
        "adaptive_strategy_counts": strategy_counts,
    }


def _apply_round_trip_cost(gross_ret: float | None) -> float | None:
    if gross_ret is None or pd.isna(gross_ret):
        return None
    slip = 5.0 / 10000.0
    comm = 0.0002
    stamp = 0.0005
    buy_fee = comm
    sell_fee = comm + stamp
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
