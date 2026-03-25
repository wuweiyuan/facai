from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

from app.features.indicators import add_indicators
from app.sector_map import load_sector_map


def should_use_sector_metrics(cfg: dict) -> bool:
    strategy = cfg.get("strategy", {})
    weights = strategy.get("weights", {})
    if float(weights.get("sector_relative", 0.0)) > 0:
        return True
    rf = cfg.get("risk_filter", {})
    return bool(rf.get("sector_relative", {}).get("enabled", False))


def load_symbol_sector_map(cfg: dict) -> dict[str, str]:
    sector_cfg = cfg.get("sector_map", {})
    path = sector_cfg.get("path")
    if not path:
        return {}
    return load_sector_map(path)


def build_sector_metrics_from_cache(
    cache_dir: str | Path,
    symbol_sector_map: dict[str, str],
    start_date: date,
    end_date: date,
) -> dict[tuple[date, str], dict[str, float]]:
    bars_dir = Path(cache_dir) / "bars"
    aggregates: dict[tuple[date, str], dict[str, float]] = defaultdict(
        lambda: {"mom20_sum": 0.0, "mom5_sum": 0.0, "count": 0}
    )
    for symbol, sector in symbol_sector_map.items():
        path = bars_dir / f"{symbol}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if df.empty:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
        if df.empty:
            continue
        df = add_indicators(df)
        for row in df.itertuples(index=False):
            trade_date = row.trade_date
            mom20 = row.mom20
            mom5 = row.mom5
            if pd.isna(mom20) or pd.isna(mom5):
                continue
            key = (trade_date, sector)
            aggregates[key]["mom20_sum"] += float(mom20)
            aggregates[key]["mom5_sum"] += float(mom5)
            aggregates[key]["count"] += 1

    out: dict[tuple[date, str], dict[str, float]] = {}
    for key, item in aggregates.items():
        count = item["count"]
        if count <= 0:
            continue
        out[key] = {
            "sector_mom20": item["mom20_sum"] / count,
            "sector_mom5": item["mom5_sum"] / count,
        }
    return out
