from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean

import pandas as pd

from app.backtest.entry_price import (
    ENTRY_PRICE_CLOSE,
    add_signal_forward_returns,
    entry_price_mode_description,
    normalize_entry_price_mode,
    signal_attempted_dates,
)
from app.config import apply_strategy_profile
from app.features.indicators import add_indicators
from app.models import BacktestRecord, StockInfo
from app.strategy.regime_risk import detect_market_state, passes_risk_filter
from app.strategy.scoring import compute_score, passes_threshold
from app.universe.filtering import filter_universe


@dataclass(frozen=True)
class AdaptiveCandidate:
    score: float
    ret_1d_gross: float | None
    ret_3d_gross: float | None
    ret_5d_gross: float | None
    ret_1d_net: float | None
    ret_3d_net: float | None
    ret_5d_net: float | None


class LocalAdaptiveCache:
    def __init__(self, cache_dir: str) -> None:
        self.root = Path(cache_dir)
        self.bars_dir = self.root / "bars"
        self.meta_dir = self.root / "meta"
        self.index_dir = self.root / "index"

    def is_available(self, index_symbol: str) -> bool:
        return (
            self.bars_dir.exists()
            and (self.meta_dir / "trade_calendar.csv").exists()
            and (self.meta_dir / "stock_list.csv").exists()
            and (self.index_dir / f"{index_symbol}.csv").exists()
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

    def load_index_closes(self, symbol: str) -> dict[date, float]:
        out: dict[date, float] = {}
        with (self.index_dir / f"{symbol}.csv").open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out[datetime.strptime(row["trade_date"], "%Y-%m-%d").date()] = float(row["close"])
        return out

    def iter_bar_files(self):
        yield from self.bars_dir.glob("*.csv")


def run_local_adaptive_backtest(
    base_cfg: dict,
    start_date: date,
    end_date: date,
    count_override: int | None = None,
    entry_price_mode: str = ENTRY_PRICE_CLOSE,
) -> dict:
    entry_price_mode = normalize_entry_price_mode(entry_price_mode)
    index_symbol = str(base_cfg.get("market_filter", {}).get("index_symbol", "000300"))
    cache_dir = str(base_cfg.get("data_source", {}).get("cache_dir", ".cache/akshare"))
    cache = LocalAdaptiveCache(cache_dir)
    if not cache.is_available(index_symbol):
        raise RuntimeError("Local adaptive cache is unavailable")

    profiles = _build_adaptive_profiles(base_cfg)
    for cfg in profiles.values():
        cfg.setdefault("data_freshness", {})["enabled"] = False

    stocks = cache.load_stock_list()
    trade_dates = [dt for dt in cache.load_trade_dates() if start_date <= dt <= end_date]
    if len(trade_dates) < 8:
        raise RuntimeError("Not enough trade dates for backtest")
    attempted_dates = signal_attempted_dates(trade_dates, entry_price_mode)
    if not attempted_dates:
        raise RuntimeError("Not enough trade dates for backtest")
    attempted_set = set(attempted_dates)
    available_symbols = {path.stem for path in cache.iter_bar_files()}
    universe_by_profile: dict[str, set[str]] = {}
    for name, cfg in profiles.items():
        universe_by_profile[name] = {
            item.symbol
            for item in filter_universe(stocks, cfg, start_date)
            if item.symbol in available_symbols
        }

    index_closes = cache.load_index_closes(index_symbol)
    market_labels = {dt: detect_market_state(index_closes, dt, base_cfg).label for dt in attempted_dates}
    market_states_by_profile = {
        name: {dt: detect_market_state(index_closes, dt, cfg) for dt in attempted_dates}
        for name, cfg in profiles.items()
    }
    order_map = base_cfg.get("adaptive_strategy", {}).get("regime_orders", {})
    pick_count_map = base_cfg.get("adaptive_strategy", {}).get("strategy_pick_counts", {})

    candidates: dict[str, dict[date, list[AdaptiveCandidate]]] = {name: defaultdict(list) for name in profiles}

    for path in cache.iter_bar_files():
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
        df = add_signal_forward_returns(df, entry_price_mode)
        df = df[df["trade_date"].isin(attempted_set)]
        if df.empty:
            continue
        for row in df.itertuples(index=False):
            latest_dict = row._asdict()
            signal_date = latest_dict["trade_date"]
            for name, cfg in profiles.items():
                if symbol not in universe_by_profile[name]:
                    continue
                latest_view = dict(latest_dict)
                latest_view["market_mom20"] = market_states_by_profile[name][signal_date].mom20
                if not passes_threshold(latest_view, "normal", cfg):
                    continue
                if not passes_risk_filter(latest_view, market_states_by_profile[name][signal_date], "normal", cfg):
                    continue
                score_total, _ = compute_score(latest_view, cfg)
                candidates[name][signal_date].append(
                    AdaptiveCandidate(
                        score=score_total,
                        ret_1d_gross=_to_float(latest_dict.get("ret_fwd_1")),
                        ret_3d_gross=_to_float(latest_dict.get("ret_fwd_3")),
                        ret_5d_gross=_to_float(latest_dict.get("ret_fwd_5")),
                        ret_1d_net=_apply_round_trip_cost(latest_dict.get("ret_fwd_1"), cfg),
                        ret_3d_net=_apply_round_trip_cost(latest_dict.get("ret_fwd_3"), cfg),
                        ret_5d_net=_apply_round_trip_cost(latest_dict.get("ret_fwd_5"), cfg),
                    )
                )

    records: list[BacktestRecord] = []
    mode_counts: Counter[str] = Counter()
    strategy_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()

    for dt in attempted_dates:
        market_label = market_labels[dt]
        ordered = order_map.get(market_label) or order_map.get("unknown") or ["recommend-pullback"]
        chosen_name: str | None = None
        picked: list[AdaptiveCandidate] = []
        for cmd_name in ordered:
            if cmd_name == "cash":
                break
            day = sorted(candidates[cmd_name].get(dt, []), key=lambda item: item.score, reverse=True)
            if not day:
                continue
            chosen_name = cmd_name
            pick_count = count_override if count_override is not None else pick_count_map.get(cmd_name, 1)
            pick_count = max(int(pick_count), 1)
            picked = day[:pick_count]
            break
        if not picked or not chosen_name:
            continue
        strategy_counts[chosen_name] += 1
        mode_counts["normal"] += 1
        records.append(
            BacktestRecord(
                trade_date=dt,
                symbol=chosen_name,
                name=chosen_name,
                threshold_mode="normal",
                ret_1d_gross=_mean([item.ret_1d_gross for item in picked]),
                ret_3d_gross=_mean([item.ret_3d_gross for item in picked]),
                ret_5d_gross=_mean([item.ret_5d_gross for item in picked]),
                ret_1d_net=_mean([item.ret_1d_net for item in picked]),
                ret_3d_net=_mean([item.ret_3d_net for item in picked]),
                ret_5d_net=_mean([item.ret_5d_net for item in picked]),
            )
        )

    summary = _build_summary(
        records,
        start_date,
        end_date,
        len(attempted_dates),
        dict(error_counts),
        [],
        dict(mode_counts),
        entry_price_mode,
    )
    summary["adaptive_strategy_counts"] = dict(strategy_counts)
    return summary


def _build_adaptive_profiles(base_cfg: dict) -> dict[str, dict]:
    profile_names = {
        "recommend": None,
        "recommend-pullback": "pullback_confirm",
        "recommend-oversold": "oversold_rebound",
        "recommend-bull": "bull_trend_research",
        "recommend-relative": "relative_strength",
    }
    overrides = base_cfg.get("adaptive_strategy", {}).get("profile_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    profiles = {}
    for name, default_profile in profile_names.items():
        profile_name = overrides.get(name, default_profile)
        profiles[name] = apply_strategy_profile(base_cfg, profile_name)
    return profiles


def _build_summary(
    records: list[BacktestRecord],
    start_date: date,
    end_date: date,
    attempted_days: int,
    error_counts: dict[str, int],
    error_examples: list[dict],
    mode_counts: dict[str, int],
    entry_price_mode: str,
) -> dict:
    one_gross = [r.ret_1d_gross for r in records if r.ret_1d_gross is not None]
    three_gross = [r.ret_3d_gross for r in records if r.ret_3d_gross is not None]
    five_gross = [r.ret_5d_gross for r in records if r.ret_5d_gross is not None]
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
        "entry_price_mode": entry_price_mode,
        "entry_price_desc": entry_price_mode_description(entry_price_mode),
        "attempted_days": attempted_days,
        "total_trades": len(records),
        "skipped_days": max(attempted_days - len(records), 0),
        "win_rate_gross_1d": _win_rate(one_gross),
        "win_rate_gross_3d": _win_rate(three_gross),
        "win_rate_net_1d": _win_rate(one_net),
        "win_rate_net_3d": _win_rate(three_net),
        "avg_return_1d_gross": _mean(one_gross) or 0.0,
        "avg_return_3d_gross": _mean(three_gross) or 0.0,
        "avg_return_5d_gross": _mean(five_gross) or 0.0,
        "avg_return_1d_net": _mean(one_net) or 0.0,
        "avg_return_3d_net": _mean(three_net) or 0.0,
        "avg_return_5d_net": _mean(five_net) or 0.0,
        "max_drawdown_proxy": max_dd,
        "threshold_mode_counts": mode_counts,
        "error_counts": error_counts,
        "error_examples": error_examples,
        "records": [],
    }


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


def _to_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


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
    net_factor = gross_factor * (1.0 - slip) * (1.0 - sell_fee) / ((1.0 + slip) * (1.0 + buy_fee))
    return net_factor - 1.0
