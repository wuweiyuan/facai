from __future__ import annotations

import numpy as np
import pandas as pd

from app.models import DailyBar


def bars_to_df(bars: list[DailyBar]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "trade_date": [b.trade_date for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "turnover_rate": [b.turnover_rate for b in bars],
        }
    )
    if df.empty:
        return df
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    diff = series.diff()
    gain = np.where(diff > 0, diff, 0.0)
    loss = np.where(diff < 0, -diff, 0.0)
    gain_avg = pd.Series(gain, index=series.index).rolling(window=window, min_periods=window).mean()
    loss_avg = pd.Series(loss, index=series.index).rolling(window=window, min_periods=window).mean()
    rs = gain_avg / loss_avg.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["ret_1d"] = out["close"].pct_change()
    out["ma20"] = out["close"].rolling(20).mean()
    out["ma60"] = out["close"].rolling(60).mean()
    out["mom5"] = out["close"] / out["close"].shift(5) - 1.0
    out["mom20"] = out["close"] / out["close"].shift(20) - 1.0
    out["rsi14"] = rsi(out["close"], 14)
    out["vol20_std"] = out["ret_1d"].rolling(20).std()
    out["ma20_slope5"] = out["ma20"] / out["ma20"].shift(5) - 1.0
    out["vol_ma5"] = out["volume"].rolling(5).mean()
    out["vol_ma20"] = out["volume"].rolling(20).mean()
    out["vol_ratio_5_20"] = out["vol_ma5"] / out["vol_ma20"]
    out["volume_std20"] = out["volume"].rolling(20).std()
    out["volume_zscore20"] = (out["volume"] - out["vol_ma20"]) / out["volume_std20"]
    prev_close = out["close"].shift(1)
    tr1 = out["high"] - out["low"]
    tr2 = (out["high"] - prev_close).abs()
    tr3 = (out["low"] - prev_close).abs()
    out["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    out["atr14"] = out["tr"].rolling(14).mean()
    return out
