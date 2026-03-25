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


def _strategy_style(cfg: dict | None) -> str:
    if not cfg:
        return "trend_following"
    return str(cfg.get("strategy", {}).get("threshold_profile", "trend_following")).strip().lower()


def threshold_from_mode(mode: str) -> ThresholdRule:
    if mode == "normal":
        return ThresholdRule(min_rsi=35, max_rsi=75, require_ma_alignment=True, min_mom20=0.0)
    if mode == "relaxed":
        return ThresholdRule(min_rsi=30, max_rsi=80, require_ma_alignment=False, min_mom20=-0.01)
    if mode == "force":
        return ThresholdRule(min_rsi=0, max_rsi=100, require_ma_alignment=False, min_mom20=-1.0)
    raise ValueError(f"Unsupported mode: {mode}")


def passes_threshold(latest: pd.Series, mode: str, cfg: dict | None = None) -> bool:
    if _strategy_style(cfg) == "oversold_rebound":
        return _passes_oversold_threshold(latest, mode)
    return _passes_trend_threshold(latest, mode)


def _passes_trend_threshold(latest: pd.Series, mode: str) -> bool:
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


def _passes_oversold_threshold(latest: pd.Series, mode: str) -> bool:
    if mode == "force":
        return True
    close = float(latest.get("close", np.nan))
    ma20 = float(latest.get("ma20", np.nan))
    mom5 = float(latest.get("mom5", np.nan))
    ret_1d = float(latest.get("ret_1d", np.nan))
    volume_ratio_1_20 = float(latest.get("volume_ratio_1_20", np.nan))
    if np.isnan(close) or np.isnan(ma20) or np.isnan(mom5) or np.isnan(ret_1d) or np.isnan(volume_ratio_1_20):
        return False
    # Keep oversold selection anchored to the validated raw event:
    # 5-day plunge, large MA20 deviation, same-day panic and visible volume expansion.
    if close > ma20 * 0.90:
        return False
    if mom5 > -0.12:
        return False
    if ret_1d > -0.03:
        return False
    if volume_ratio_1_20 < 1.3:
        return False
    return True


def _clip01(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return float(np.clip((v - lo) / (hi - lo), 0.0, 1.0))


def _centered_score(v: float, center: float, half_width: float) -> float:
    if half_width <= 0:
        return 0.0
    return float(np.clip(1.0 - abs(v - center) / half_width, 0.0, 1.0))


def compute_score(latest: pd.Series, cfg: dict) -> tuple[float, dict[str, float]]:
    strategy = cfg.get("strategy", {})
    w = strategy.get(
        "weights",
        {"trend": 0.35, "momentum": 0.35, "stability": 0.15, "volume": 0.15},
    )
    close = float(latest.get("close", 0.0))
    ma20 = float(latest.get("ma20", close))
    ma60 = float(latest.get("ma60", ma20))
    mom5 = float(latest.get("mom5", 0.0))
    mom20 = float(latest.get("mom20", 0.0))
    rsi14 = float(latest.get("rsi14", 50.0))
    vol20_std = float(latest.get("vol20_std", 0.03))
    ma20_slope5 = float(latest.get("ma20_slope5", 0.0))
    vol_ratio_5_20 = float(latest.get("vol_ratio_5_20", 1.0))
    volume_ratio_1_20 = float(latest.get("volume_ratio_1_20", 1.0))
    volume_zscore20 = float(latest.get("volume_zscore20", 0.0))
    ret_1d = float(latest.get("ret_1d", 0.0))
    market_mom20 = float(latest.get("market_mom20", 0.0))
    sector_mom20 = float(latest.get("sector_mom20", 0.0))

    if np.isnan(ma20):
        ma20 = close
    if np.isnan(ma60):
        ma60 = ma20
    if np.isnan(mom5):
        mom5 = 0.0
    if np.isnan(mom20):
        mom20 = 0.0
    if np.isnan(rsi14):
        rsi14 = 50.0
    if np.isnan(vol20_std):
        vol20_std = 0.03
    if np.isnan(ma20_slope5):
        ma20_slope5 = 0.0
    if np.isnan(vol_ratio_5_20):
        vol_ratio_5_20 = 1.0
    if np.isnan(volume_ratio_1_20):
        volume_ratio_1_20 = 1.0
    if np.isnan(volume_zscore20):
        volume_zscore20 = 0.0
    if np.isnan(ret_1d):
        ret_1d = 0.0
    if np.isnan(market_mom20):
        market_mom20 = 0.0
    if np.isnan(sector_mom20):
        sector_mom20 = 0.0

    trend = (
        _clip01(close / ma20 - 1.0, -0.03, 0.08) * 0.4
        + _clip01(ma20 / ma60 - 1.0, -0.03, 0.08) * 0.4
        + _clip01(ma20_slope5, -0.02, 0.04) * 0.2
    ) * 100
    momentum = (_clip01(mom5, -0.08, 0.12) * 0.5 + _clip01(mom20, -0.15, 0.25) * 0.5) * 100
    stability = (1.0 - _clip01(vol20_std, 0.01, 0.08)) * 100
    volume = (_clip01(vol_ratio_5_20, 0.8, 2.0) * 0.6 + _clip01(volume_zscore20, -0.5, 2.5) * 0.4) * 100
    distance_above_ma20 = close / ma20 - 1.0 if ma20 > 0 else 0.0
    pullback = (
        _centered_score(distance_above_ma20, 0.01, 0.03) * 0.5
        + _centered_score(mom5, 0.0, 0.05) * 0.3
        + _centered_score(rsi14, 55.0, 18.0) * 0.2
    ) * 100
    relative_strength = (
        _clip01(mom20 - market_mom20, -0.01, 0.15) * 0.7
        + _clip01(mom5, -0.03, 0.10) * 0.3
    ) * 100
    sector_relative = (
        _clip01(mom20 - sector_mom20, -0.01, 0.12) * 0.7
        + _clip01(sector_mom20, -0.03, 0.12) * 0.3
    ) * 100
    distance_below_ma20 = max(1.0 - close / ma20, 0.0) if ma20 > 0 else 0.0
    # Rank oversold names by being close to the validated setup, not by being
    # "the most broken" stock on the board.
    oversold_raw = (
        _centered_score(distance_below_ma20, 0.11, 0.05) * 0.30
        + _centered_score(-mom5, 0.14, 0.06) * 0.25
        + _centered_score(-ret_1d, 0.045, 0.025) * 0.20
        + _centered_score(volume_ratio_1_20, 1.6, 0.6) * 0.15
        + _centered_score(rsi14, 24.0, 18.0) * 0.10
    ) * 100
    # In recent local-cache samples, the lower-ranked oversold names outperformed
    # the higher-ranked ones under the original direction, so keep the validated
    # hard filter and flip ranking preference to favor the milder panic setups.
    oversold = 100.0 - oversold_raw

    score_breakdown = {
        "trend": trend,
        "momentum": momentum,
        "stability": stability,
        "volume": volume,
    }
    if float(w.get("pullback", 0.0)) > 0:
        score_breakdown["pullback"] = pullback
    if float(w.get("oversold", 0.0)) > 0:
        score_breakdown["oversold"] = oversold
    if float(w.get("relative_strength", 0.0)) > 0:
        score_breakdown["relative_strength"] = relative_strength
    if float(w.get("sector_relative", 0.0)) > 0:
        score_breakdown["sector_relative"] = sector_relative
    weights = {
        "trend": float(w.get("trend", 0.35)),
        "momentum": float(w.get("momentum", 0.35)),
        "stability": float(w.get("stability", 0.15)),
        "volume": float(w.get("volume", 0.15)),
        "pullback": float(w.get("pullback", 0.0)),
        "oversold": float(w.get("oversold", 0.0)),
        "relative_strength": float(w.get("relative_strength", 0.0)),
        "sector_relative": float(w.get("sector_relative", 0.0)),
    }
    weight_sum = sum(max(v, 0.0) for v in weights.values()) or 1.0
    total = (
        trend * max(weights["trend"], 0.0)
        + momentum * max(weights["momentum"], 0.0)
        + stability * max(weights["stability"], 0.0)
        + volume * max(weights["volume"], 0.0)
        + pullback * max(weights["pullback"], 0.0)
        + oversold * max(weights["oversold"], 0.0)
        + relative_strength * max(weights["relative_strength"], 0.0)
        + sector_relative * max(weights["sector_relative"], 0.0)
    ) / weight_sum
    return float(total), score_breakdown


def build_reason(latest: pd.Series, score_breakdown: dict[str, float], mode: str, cfg: dict | None = None) -> list[str]:
    if _strategy_style(cfg) == "oversold_rebound":
        reasons = [
            f"超跌反弹分 {score_breakdown.get('oversold', 0.0):.1f}，更符合急跌、偏离均线后的技术修复候选。",
            f"收盘位于MA20下方，5日动量 {float(latest.get('mom5', 0.0)):.2%}，RSI {float(latest.get('rsi14', 50.0)):.1f}。",
            f"量能分 {score_breakdown.get('volume', 0.0):.1f}，当日成交量相对20日均量放大，具备恐慌释放特征。",
        ]
        if mode == "relaxed":
            reasons.append("今日超跌候选较少，已启用放宽阈值模式。")
        if mode == "force":
            reasons.append("常规超跌筛选无结果，已启用强制推荐兜底。")
        return reasons
    reasons = [
        f"趋势分 {score_breakdown['trend']:.1f}，收盘价高于MA20且中期均线结构较稳。",
        f"动量分 {score_breakdown['momentum']:.1f}，5日/20日动量维持正向。",
        f"波动稳定分 {score_breakdown['stability']:.1f}，近20日波动处于可接受范围。",
        f"量能分 {score_breakdown['volume']:.1f}，成交量相对均量结构较健康。",
    ]
    if "pullback" in score_breakdown:
        reasons.append(f"回踩确认分 {score_breakdown['pullback']:.1f}，股价更接近均线而非追高位置。")
    if "relative_strength" in score_breakdown:
        reasons.append(f"相对强弱分 {score_breakdown['relative_strength']:.1f}，个股近期表现强于市场基准。")
    if "sector_relative" in score_breakdown:
        reasons.append(f"板块相对强弱分 {score_breakdown['sector_relative']:.1f}，个股近期表现强于所属板块。")
    if mode == "relaxed":
        reasons.append("今日候选较少，已启用放宽阈值模式。")
    if mode == "force":
        reasons.append("常规与放宽筛选均无结果，已启用强制推荐兜底。")
    return reasons
