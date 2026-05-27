from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategy.regime_risk import MarketState


def suggest_holding_days(latest: pd.Series, market_state: MarketState, cfg: dict | None = None) -> int:
    style = str((cfg or {}).get("strategy", {}).get("threshold_profile", "trend_following")).strip().lower()
    if style == "oversold_rebound":
        return 3
    if style == "pullback_confirm":
        if market_state.label == "bull":
            return 4
        return 3

    mom20 = float(latest.get("mom20", 0.0))
    vol20 = float(latest.get("vol20_std", 0.05))
    rsi14 = float(latest.get("rsi14", 50.0))
    if np.isnan(mom20):
        mom20 = 0.0
    if np.isnan(vol20):
        vol20 = 0.05
    if np.isnan(rsi14):
        rsi14 = 50.0

    # Base holding days by trend strength and volatility.
    if mom20 >= 0.10 and vol20 <= 0.03 and rsi14 <= 70:
        days = 5
    elif mom20 >= 0.04 and vol20 <= 0.05 and rsi14 <= 78:
        days = 3
    else:
        days = 2

    # Risk-off regime: shorten holding period.
    if market_state.label == "bear":
        days = min(days, 1)
    elif market_state.label == "neutral":
        days = min(days, 3)
    return max(days, 1)


def build_exit_plan(latest: pd.Series, market_state: MarketState, cfg: dict | None = None) -> str:
    style = str((cfg or {}).get("strategy", {}).get("threshold_profile", "trend_following")).strip().lower()
    if style == "oversold_rebound":
        return "默认持有2到3天；2天内不修复或亏损5%到6%止损；快速反弹5%到8%止盈。"
    if style == "pullback_confirm":
        return "默认持有3到4天；跌回关键均线或2到3天不走强就退出；到压力位滞涨可分批止盈。"

    days = suggest_holding_days(latest, market_state, cfg)
    if days <= 1:
        return "默认持有1天；买后不强就退出；跌回MA20下方或单笔亏损4%到5%止损。"
    if days == 2:
        return "默认持有2天；买后不强就退出；跌回MA20下方或单笔亏损4%到5%止损。"
    if days == 3:
        return "默认持有3天；买后1到2天不强或跌回MA20附近转弱就退出；放量冲高回落可分批止盈。"
    return f"默认持有{days}天；买后2天内跌回MA20附近转弱就退出；加速放量冲高回落可分批止盈。"
