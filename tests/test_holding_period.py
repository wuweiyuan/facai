from __future__ import annotations

from unittest import TestCase

import pandas as pd

from app.strategy.holding_period import build_exit_plan, suggest_holding_days
from app.strategy.regime_risk import MarketState


class TestHoldingPeriod(TestCase):
    def test_exit_plan_uses_suggested_days_when_bull_market_stock_scores_two_days(self):
        latest = pd.Series({"mom20": 0.01, "vol20_std": 0.06, "rsi14": 55.0})
        market = MarketState(label="bull", close=100.0, ma20=95.0, ma60=90.0, mom20=0.05)

        suggested_days = suggest_holding_days(latest, market, {})

        self.assertEqual(suggested_days, 2)
        self.assertIn("默认持有2天", build_exit_plan(latest, market, {}))

    def test_exit_plan_uses_suggested_days_when_neutral_market_stock_scores_three_days(self):
        latest = pd.Series({"mom20": 0.06, "vol20_std": 0.04, "rsi14": 60.0})
        market = MarketState(label="neutral", close=100.0, ma20=99.0, ma60=98.0, mom20=0.01)

        suggested_days = suggest_holding_days(latest, market, {})

        self.assertEqual(suggested_days, 3)
        self.assertIn("默认持有3天", build_exit_plan(latest, market, {}))
