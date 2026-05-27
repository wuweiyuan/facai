from __future__ import annotations

from unittest import TestCase

import pandas as pd

from app.strategy.regime_risk import MarketState, passes_risk_filter
from app.strategy.regime_risk import detect_market_state


class TestRegimeRisk(TestCase):
    def test_detect_market_state_can_require_bull_close_buffer_above_ma20(self):
        closes = {idx: 100.0 for idx in range(1, 81)}
        for idx in range(81, 101):
            closes[idx] = 100.0 + (idx - 80) * 0.5
        cfg = {"market_filter": {"lookback_days": 120, "bull_min_close_above_ma20_pct": 0.05}}

        state = detect_market_state(closes, 100, cfg)

        self.assertEqual(state.label, "neutral")

    def test_pullback_filter_rejects_signal_day_drop_below_min_ret_1d(self):
        latest = pd.Series(
            {
                "close": 10.0,
                "ma20": 9.9,
                "rsi14": 55.0,
                "vol20_std": 0.02,
                "vol_ratio_5_20": 1.0,
                "volume_ratio_1_20": 1.0,
                "mom5": -0.01,
                "mom20": 0.05,
                "ret_1d": -0.05,
                "volume_zscore20": 0.5,
                "turnover_rate": 2.0,
            }
        )
        market = MarketState(label="bull", close=100.0, ma20=98.0, ma60=95.0, mom20=0.04)
        cfg = {
            "risk_filter": {
                "enabled": True,
                "pullback": {
                    "enabled": True,
                    "min_close_above_ma20_pct": 0.0,
                    "max_close_above_ma20_pct": 0.05,
                    "min_mom20": 0.0,
                    "max_mom20": 0.18,
                    "min_mom5": -0.04,
                    "max_mom5": 0.05,
                    "min_ret_1d": -0.04,
                },
            }
        }

        self.assertFalse(passes_risk_filter(latest, market, "normal", cfg))

    def test_pullback_filter_accepts_signal_day_drop_above_min_ret_1d(self):
        latest = pd.Series(
            {
                "close": 10.0,
                "ma20": 9.9,
                "rsi14": 55.0,
                "vol20_std": 0.02,
                "vol_ratio_5_20": 1.0,
                "volume_ratio_1_20": 1.0,
                "mom5": -0.01,
                "mom20": 0.05,
                "ret_1d": -0.03,
                "volume_zscore20": 0.5,
                "turnover_rate": 2.0,
            }
        )
        market = MarketState(label="bull", close=100.0, ma20=98.0, ma60=95.0, mom20=0.04)
        cfg = {
            "risk_filter": {
                "enabled": True,
                "pullback": {
                    "enabled": True,
                    "min_close_above_ma20_pct": 0.0,
                    "max_close_above_ma20_pct": 0.05,
                    "min_mom20": 0.0,
                    "max_mom20": 0.18,
                    "min_mom5": -0.04,
                    "max_mom5": 0.05,
                    "min_ret_1d": -0.04,
                },
            }
        }

        self.assertTrue(passes_risk_filter(latest, market, "normal", cfg))
