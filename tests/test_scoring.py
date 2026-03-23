from __future__ import annotations

from unittest import TestCase

import pandas as pd

from app.strategy.scoring import compute_score, passes_threshold


class TestScoring(TestCase):
    def test_passes_threshold_normal(self):
        latest = pd.Series(
            {
                "close": 11.0,
                "ma20": 10.0,
                "ma60": 9.5,
                "mom20": 0.05,
                "rsi14": 60.0,
                "mom5": 0.03,
                "vol20_std": 0.02,
                "ma20_slope5": 0.01,
            }
        )
        self.assertTrue(passes_threshold(latest, "normal"))
        total, breakdown = compute_score(latest, {"strategy": {"weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}}})
        self.assertGreater(total, 0)
        self.assertIn("trend", breakdown)

    def test_fails_threshold_when_rsi_too_high(self):
        latest = pd.Series(
            {
                "close": 11.0,
                "ma20": 10.0,
                "ma60": 9.5,
                "mom20": 0.05,
                "rsi14": 90.0,
                "mom5": 0.03,
                "vol20_std": 0.02,
                "ma20_slope5": 0.01,
            }
        )
        self.assertFalse(passes_threshold(latest, "normal"))

    def test_passes_threshold_for_oversold_profile(self):
        latest = pd.Series(
            {
                "close": 8.7,
                "ma20": 10.0,
                "ma60": 10.5,
                "mom20": -0.08,
                "mom5": -0.15,
                "ret_1d": -0.04,
                "rsi14": 28.0,
                "vol20_std": 0.03,
            }
        )

        self.assertTrue(passes_threshold(latest, "normal", {"strategy": {"threshold_profile": "oversold_rebound"}}))
