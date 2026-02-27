from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ThresholdRule:
    min_rsi: float
    max_rsi: float
    require_ma_alignment: bool
    min_mom20: float


def threshold_from_mode(mode: str) -> ThresholdRule:
    if mode == "normal":
        return ThresholdRule(min_rsi=35, max_rsi=75, require_ma_alignment=True, min_mom20=0.0)
    if mode == "relaxed":
        return ThresholdRule(min_rsi=30, max_rsi=80, require_ma_alignment=False, min_mom20=-0.01)
    if mode == "force":
        return ThresholdRule(min_rsi=0, max_rsi=100, require_ma_alignment=False, min_mom20=-1.0)
    raise ValueError(f"Unsupported mode: {mode}")


def passes_threshold(latest: pd.Series, mode: str) -> bool:
    rule = threshold_from_mode(mode)
    if np.isnan(latest["ma20"]) or np.isnan(latest["ma60"]):
        return False
    if latest["close"] <= latest["ma20"]:
        return False
    if rule.require_ma_alignment and latest["ma20"] <= latest["ma60"]:
        return False
    if latest["mom20"] <= rule.min_mom20:
        return False
    if not (rule.min_rsi <= latest["rsi14"] <= rule.max_rsi):
        return False
    return True


def _clip01(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return float(np.clip((v - lo) / (hi - lo), 0.0, 1.0))


def compute_score(latest: pd.Series, cfg: dict) -> tuple[float, dict[str, float]]:
    strategy = cfg.get("strategy", {})
    w = strategy.get("weights", {"trend": 0.35, "momentum": 0.35, "stability": 0.15, "volume": 0.15})
    close = float(latest.get("close", 0.0))
    ma20 = float(latest.get("ma20", close))
    ma60 = float(latest.get("ma60", ma20))
    mom5 = float(latest.get("mom5", 0.0))
    mom20 = float(latest.get("mom20", 0.0))
    vol20_std = float(latest.get("vol20_std", 0.03))
    ma20_slope5 = float(latest.get("ma20_slope5", 0.0))
    vol_ratio_5_20 = float(latest.get("vol_ratio_5_20", 1.0))
    volume_zscore20 = float(latest.get("volume_zscore20", 0.0))

    if np.isnan(ma20):
        ma20 = close
    if np.isnan(ma60):
        ma60 = ma20
    if np.isnan(mom5):
        mom5 = 0.0
    if np.isnan(mom20):
        mom20 = 0.0
    if np.isnan(vol20_std):
        vol20_std = 0.03
    if np.isnan(ma20_slope5):
        ma20_slope5 = 0.0
    if np.isnan(vol_ratio_5_20):
        vol_ratio_5_20 = 1.0
    if np.isnan(volume_zscore20):
        volume_zscore20 = 0.0

    trend = (
        _clip01(close / ma20 - 1.0, -0.03, 0.08) * 0.4
        + _clip01(ma20 / ma60 - 1.0, -0.03, 0.08) * 0.4
        + _clip01(ma20_slope5, -0.02, 0.04) * 0.2
    ) * 100
    momentum = (_clip01(mom5, -0.08, 0.12) * 0.5 + _clip01(mom20, -0.15, 0.25) * 0.5) * 100
    stability = (1.0 - _clip01(vol20_std, 0.01, 0.08)) * 100
    volume = (_clip01(vol_ratio_5_20, 0.8, 2.0) * 0.6 + _clip01(volume_zscore20, -0.5, 2.5) * 0.4) * 100

    score_breakdown = {
        "trend": trend,
        "momentum": momentum,
        "stability": stability,
        "volume": volume,
    }
    weights = {
        "trend": float(w.get("trend", 0.35)),
        "momentum": float(w.get("momentum", 0.35)),
        "stability": float(w.get("stability", 0.15)),
        "volume": float(w.get("volume", 0.15)),
    }
    weight_sum = sum(max(v, 0.0) for v in weights.values()) or 1.0
    total = (
        trend * max(weights["trend"], 0.0)
        + momentum * max(weights["momentum"], 0.0)
        + stability * max(weights["stability"], 0.0)
        + volume * max(weights["volume"], 0.0)
    ) / weight_sum
    return float(total), score_breakdown


def build_reason(latest: pd.Series, score_breakdown: dict[str, float], mode: str) -> list[str]:
    reasons = [
        f"趋势分 {score_breakdown['trend']:.1f}，收盘价高于MA20且中期均线结构较稳。",
        f"动量分 {score_breakdown['momentum']:.1f}，5日/20日动量维持正向。",
        f"波动稳定分 {score_breakdown['stability']:.1f}，近20日波动处于可接受范围。",
        f"量能分 {score_breakdown['volume']:.1f}，成交量相对均量结构较健康。",
    ]
    if mode == "relaxed":
        reasons.append("今日候选较少，已启用放宽阈值模式。")
    if mode == "force":
        reasons.append("常规与放宽筛选均无结果，已启用强制推荐兜底。")
    return reasons
