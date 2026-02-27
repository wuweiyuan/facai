from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MarketState:
    label: str
    close: float
    ma20: float
    ma60: float
    mom20: float


def detect_market_state(index_closes: dict, signal_date, cfg: dict) -> MarketState:
    mcfg = cfg.get("market_filter", {})
    lookback = int(mcfg.get("lookback_days", 120))
    items = [(d, c) for d, c in index_closes.items() if d <= signal_date]
    items.sort(key=lambda x: x[0])
    if not items:
        return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0)
    series = pd.Series([float(c) for _, c in items][-lookback:])
    if series.empty:
        return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0)
    close = float(series.iloc[-1])
    ma20 = float(series.rolling(20).mean().iloc[-1]) if len(series) >= 20 else close
    ma60 = float(series.rolling(60).mean().iloc[-1]) if len(series) >= 60 else ma20
    mom20 = float(series.iloc[-1] / series.iloc[-21] - 1.0) if len(series) >= 21 else 0.0
    if close > ma20 > ma60 and mom20 > 0:
        label = "bull"
    elif close < ma20 and mom20 < 0:
        label = "bear"
    else:
        label = "neutral"
    return MarketState(label=label, close=close, ma20=ma20, ma60=ma60, mom20=mom20)


def passes_risk_filter(latest: pd.Series, market: MarketState, mode: str, cfg: dict) -> bool:
    rcfg = cfg.get("risk_filter", {})
    if not bool(rcfg.get("enabled", True)):
        return True
    # Keep force mode as emergency fallback path.
    if mode == "force":
        return True

    market_cfg = cfg.get("market_filter", {})
    if bool(market_cfg.get("enabled", True)) and market.label == "bear" and bool(market_cfg.get("block_on_bear", True)):
        return False

    close = _to_float(latest.get("close", np.nan), np.nan)
    rsi14 = _to_float(latest.get("rsi14", np.nan), np.nan)
    vol20_std = _to_float(latest.get("vol20_std", np.nan), np.nan)
    vol_ratio = _to_float(latest.get("vol_ratio_5_20", np.nan), np.nan)
    mom20 = _to_float(latest.get("mom20", np.nan), np.nan)
    turnover = _to_float(latest.get("turnover_rate", 0.0), 0.0)

    if np.isnan(close) or np.isnan(rsi14) or np.isnan(vol20_std) or np.isnan(vol_ratio) or np.isnan(mom20):
        return False
    if close < float(rcfg.get("min_price", 2.0)):
        return False
    if close > float(rcfg.get("max_price", 200.0)):
        return False
    if rsi14 > float(rcfg.get("rsi_upper", 85.0)):
        return False
    if vol20_std > float(rcfg.get("max_vol20_std", 0.07)):
        return False
    if vol_ratio < float(rcfg.get("min_vol_ratio_5_20", 0.6)):
        return False
    if bool(rcfg.get("require_turnover_data", False)):
        if turnover <= float(rcfg.get("min_turnover_rate", 0.0)):
            return False

    weak_cfg = rcfg.get("weak_market", {})
    if market.label in {"bear", "neutral"} and bool(weak_cfg.get("enabled", True)):
        if vol20_std > float(weak_cfg.get("max_vol20_std", 0.05)):
            return False
        if bool(weak_cfg.get("require_mom20_positive", False)) and mom20 <= 0:
            return False
    return True


def _to_float(v, default: float) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default
