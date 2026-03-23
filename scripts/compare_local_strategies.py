from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import apply_strategy_profile, load_config
from app.features.indicators import add_indicators
from app.models import StockInfo
from app.strategy.regime_risk import detect_market_state, passes_risk_filter
from app.strategy.scoring import compute_score, passes_threshold
from app.universe.filtering import filter_universe


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _deepcopy_cfg(cfg: dict) -> dict:
    return json.loads(json.dumps(cfg))


@dataclass(frozen=True)
class CandidateRecord:
    score: float
    ret_1d_net: float | None
    ret_3d_net: float | None
    ret_5d_net: float | None


class LocalCacheLoader:
    def __init__(self, cache_dir: str) -> None:
        self.root = Path(cache_dir)
        self.bars_dir = self.root / "bars"
        self.meta_dir = self.root / "meta"
        self.index_dir = self.root / "index"

    def load_stock_list(self) -> list[StockInfo]:
        path = self.meta_dir / "stock_list.csv"
        items: list[StockInfo] = []
        with path.open("r", encoding="utf-8", newline="") as f:
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
        path = self.meta_dir / "trade_calendar.csv"
        out: list[date] = []
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out.append(_parse_date(row["trade_date"]))
        out.sort()
        return out

    def load_index_closes(self, symbol: str) -> dict[date, float]:
        path = self.index_dir / f"{symbol}.csv"
        out: dict[date, float] = {}
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out[_parse_date(row["trade_date"])] = float(row["close"])
        return out

    def iter_bar_files(self):
        yield from self.bars_dir.glob("*.csv")


def _resolve_window(trade_dates: list[date], start: str | None, end: str | None, months: int) -> tuple[date, date]:
    if not trade_dates:
        raise RuntimeError("No trade dates found in local cache.")
    end_date = _parse_date(end) if end else trade_dates[-1]
    if start:
        start_date = _parse_date(start)
    else:
        start_date = end_date - timedelta(days=max(months, 1) * 31)
    if start_date >= end_date:
        raise RuntimeError("start date must be earlier than end date.")
    return start_date, end_date


def _resolve_market_states(index_closes: dict[date, float], attempted_dates: list[date], profiles: dict[str, dict]) -> dict[str, dict[date, object]]:
    out: dict[str, dict[date, object]] = {}
    for name, cfg in profiles.items():
        out[name] = {dt: detect_market_state(index_closes, dt, cfg) for dt in attempted_dates}
    return out


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


def _build_profiles(base_cfg: dict) -> dict[str, dict]:
    profiles = {
        "recommend": _deepcopy_cfg(base_cfg),
        "recommend-pullback": apply_strategy_profile(base_cfg, "pullback_confirm"),
        "recommend-oversold": apply_strategy_profile(base_cfg, "oversold_rebound"),
    }
    for cfg in profiles.values():
        cfg.setdefault("data_freshness", {})["enabled"] = False
    return profiles


def _select_universe(stocks: list[StockInfo], profiles: dict[str, dict], bars_dir: Path, as_of_date: date) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    available_symbols = {p.stem for p in bars_dir.glob("*.csv")}
    for name, cfg in profiles.items():
        selected = filter_universe(stocks, cfg, as_of_date)
        out[name] = {item.symbol for item in selected if item.symbol in available_symbols}
    return out


def _scan_candidates(
    loader: LocalCacheLoader,
    profiles: dict[str, dict],
    universe_by_profile: dict[str, set[str]],
    attempted_dates: list[date],
    start_date: date,
    end_date: date,
    market_states: dict[str, dict[date, object]],
) -> dict[str, dict[date, list[CandidateRecord]]]:
    attempted_set = set(attempted_dates)
    candidates: dict[str, dict[date, list[CandidateRecord]]] = {name: defaultdict(list) for name in profiles}

    for path in loader.iter_bar_files():
        symbol = path.stem
        if not any(symbol in symbols for symbols in universe_by_profile.values()):
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
            latest = row._asdict()
            signal_date = latest["trade_date"]
            for name, cfg in profiles.items():
                if symbol not in universe_by_profile[name]:
                    continue
                if not passes_threshold(latest, "normal", cfg):
                    continue
                if not passes_risk_filter(latest, market_states[name][signal_date], "normal", cfg):
                    continue
                score_total, _ = compute_score(latest, cfg)
                candidates[name][signal_date].append(
                    CandidateRecord(
                        score=score_total,
                        ret_1d_net=_apply_round_trip_cost(latest["ret_fwd_1"], cfg),
                        ret_3d_net=_apply_round_trip_cost(latest["ret_fwd_3"], cfg),
                        ret_5d_net=_apply_round_trip_cost(latest["ret_fwd_5"], cfg),
                    )
                )
    return candidates


def _mean(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _win_rate(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(1 for v in nums if v > 0) / len(nums)


def _summarize_counts(
    profiles: dict[str, dict],
    candidates: dict[str, dict[date, list[CandidateRecord]]],
    attempted_dates: list[date],
    counts: list[int],
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for count in counts:
        bucket: dict[str, object] = {}
        for name in profiles:
            one: list[float | None] = []
            three: list[float | None] = []
            five: list[float | None] = []
            equity = 1.0
            peak = 1.0
            max_dd = 0.0
            trades = 0
            candidate_days = 0
            candidate_total = 0

            for dt in attempted_dates:
                day = sorted(candidates[name].get(dt, []), key=lambda item: item.score, reverse=True)
                if not day:
                    continue
                candidate_days += 1
                candidate_total += len(day)
                picked = day[:count]
                trades += 1
                avg1 = _mean([item.ret_1d_net for item in picked])
                avg3 = _mean([item.ret_3d_net for item in picked])
                avg5 = _mean([item.ret_5d_net for item in picked])
                one.append(avg1)
                three.append(avg3)
                five.append(avg5)
                if avg1 is not None:
                    equity *= 1.0 + avg1
                    peak = max(peak, equity)
                    max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)

            bucket[name] = {
                "total_trades": trades,
                "skipped_days": len(attempted_dates) - trades,
                "win_rate_net_1d": _win_rate(one),
                "win_rate_net_3d": _win_rate(three),
                "avg_return_1d_net": _mean(one),
                "avg_return_3d_net": _mean(three),
                "avg_return_5d_net": _mean(five),
                "max_drawdown_proxy": max_dd,
                "avg_candidates_per_trade_day": (candidate_total / candidate_days) if candidate_days else 0.0,
            }
        out[f"count_{count}"] = bucket
    return out


def _summarize_score_quality(candidates: dict[str, dict[date, list[CandidateRecord]]]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for name, by_date in candidates.items():
        rows: list[CandidateRecord] = []
        for items in by_date.values():
            rows.extend(items)
        rows = [item for item in rows if item.ret_5d_net is not None]
        rows.sort(key=lambda item: item.score)
        n = len(rows)
        cut = int(n * 0.2)
        bottom = rows[:cut] if cut > 0 else []
        top = rows[-cut:] if cut > 0 else []
        out[name] = {
            "all_r5": {
                "n": len(rows),
                "win": _win_rate([item.ret_5d_net for item in rows]),
                "avg": _mean([item.ret_5d_net for item in rows]),
            },
            "top20pct_by_score_r5": {
                "n": len(top),
                "win": _win_rate([item.ret_5d_net for item in top]),
                "avg": _mean([item.ret_5d_net for item in top]),
            },
            "bottom20pct_by_score_r5": {
                "n": len(bottom),
                "win": _win_rate([item.ret_5d_net for item in bottom]),
                "avg": _mean([item.ret_5d_net for item in bottom]),
            },
        }
    return out


def _round_payload(value):
    if isinstance(value, dict):
        return {k: _round_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round_payload(v) for v in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


def _render_table(payload: dict) -> str:
    lines: list[str] = []
    lines.append(f"Period: {payload['period'][0]} -> {payload['period'][1]}")
    lines.append(f"Attempted trade days: {payload['attempted_days']}")
    lines.append("")
    for count_key, strategies in payload["count_comparison"].items():
        lines.append(f"[{count_key}]")
        lines.append(
            "strategy              trades  skip  win1    avg1    win3    avg3    avg5    maxdd   avg_candidates"
        )
        for name, stats in strategies.items():
            lines.append(
                f"{name:<21} "
                f"{stats['total_trades']:>6} "
                f"{stats['skipped_days']:>5} "
                f"{_fmt(stats['win_rate_net_1d']):>7} "
                f"{_fmt(stats['avg_return_1d_net']):>7} "
                f"{_fmt(stats['win_rate_net_3d']):>7} "
                f"{_fmt(stats['avg_return_3d_net']):>7} "
                f"{_fmt(stats['avg_return_5d_net']):>7} "
                f"{_fmt(stats['max_drawdown_proxy']):>7} "
                f"{_fmt(stats['avg_candidates_per_trade_day']):>14}"
            )
        lines.append("")
    lines.append("[score_quality_r5]")
    lines.append("strategy              all_avg  top20_avg  bottom20_avg  top20_win  bottom20_win")
    for name, stats in payload["score_quality"].items():
        lines.append(
            f"{name:<21} "
            f"{_fmt(stats['all_r5']['avg']):>7} "
            f"{_fmt(stats['top20pct_by_score_r5']['avg']):>10} "
            f"{_fmt(stats['bottom20pct_by_score_r5']['avg']):>13} "
            f"{_fmt(stats['top20pct_by_score_r5']['win']):>10} "
            f"{_fmt(stats['bottom20pct_by_score_r5']['win']):>13}"
        )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare local-cache strategy performance without network access.")
    parser.add_argument("--config", default="config/default.yaml", help="Path to YAML config")
    parser.add_argument("--cache-dir", default=".cache/akshare", help="Local akshare cache directory")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--months", type=int, default=6, help="Default lookback window in months when --start is omitted")
    parser.add_argument("--counts", default="1,3", help="Comma-separated pick counts, e.g. 1,3")
    parser.add_argument("--output", choices=["table", "json"], default="table")
    parser.add_argument("--save", default=None, help="Optional path to save the result")
    args = parser.parse_args()

    base_cfg = load_config(args.config)
    profiles = _build_profiles(base_cfg)
    loader = LocalCacheLoader(args.cache_dir)
    trade_dates = loader.load_trade_dates()
    start_date, end_date = _resolve_window(trade_dates, args.start, args.end, args.months)
    trade_dates = [dt for dt in trade_dates if start_date <= dt <= end_date]
    if len(trade_dates) < 8:
        raise RuntimeError("Not enough trade dates in the selected window; need at least 8.")
    attempted_dates = trade_dates[:-5]
    counts = [max(int(part.strip()), 1) for part in args.counts.split(",") if part.strip()]

    stocks = loader.load_stock_list()
    universe_by_profile = _select_universe(stocks, profiles, loader.bars_dir, start_date)
    index_symbol = str(base_cfg.get("market_filter", {}).get("index_symbol", "000300"))
    index_closes = loader.load_index_closes(index_symbol)
    market_states = _resolve_market_states(index_closes, attempted_dates, profiles)
    candidates = _scan_candidates(
        loader=loader,
        profiles=profiles,
        universe_by_profile=universe_by_profile,
        attempted_dates=attempted_dates,
        start_date=start_date,
        end_date=end_date,
        market_states=market_states,
    )

    payload = _round_payload(
        {
            "period": [start_date.isoformat(), end_date.isoformat()],
            "attempted_days": len(attempted_dates),
            "count_comparison": _summarize_counts(profiles, candidates, attempted_dates, counts),
            "score_quality": _summarize_score_quality(candidates),
        }
    )

    rendered = json.dumps(payload, ensure_ascii=False, indent=2) if args.output == "json" else _render_table(payload)
    print(rendered)
    if args.save:
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(rendered + ("\n" if not rendered.endswith("\n") else ""), encoding="utf-8")


if __name__ == "__main__":
    main()
