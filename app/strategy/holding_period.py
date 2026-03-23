from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategy.regime_risk import MarketState


def suggest_holding_days(latest: pd.Series, market_state: MarketState, cfg: dict | None = None) -> int:
    style = str((cfg or {}).get("strategy", {}).get("threshold_profile", "trend_following")).strip().lower()
    if style == "oversold_rebound":
        if market_state.label == "bear":
            return 4
        return 5

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
