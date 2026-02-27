from __future__ import annotations

from datetime import date, timedelta
from unittest import TestCase

from app.features.indicators import add_indicators, bars_to_df
from app.models import DailyBar


class TestIndicators(TestCase):
    def test_add_indicators_has_expected_columns(self):
        bars = []
        start = date(2025, 1, 1)
        px = 10.0
        for i in range(80):
            px *= 1.002
            bars.append(
                DailyBar(
                    trade_date=start + timedelta(days=i),
                    open=px * 0.99,
                    high=px * 1.01,
                    low=px * 0.98,
                    close=px,
                    volume=1_000_000,
                    turnover_rate=1.5,
                )
            )
        df = add_indicators(bars_to_df(bars))
        self.assertIn("ma20", df.columns)
        self.assertIn("ma60", df.columns)
        self.assertIn("mom5", df.columns)
        self.assertIn("mom20", df.columns)
        self.assertIn("rsi14", df.columns)
        self.assertIn("vol20_std", df.columns)

