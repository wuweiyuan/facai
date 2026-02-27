from __future__ import annotations

from datetime import date, timedelta
from unittest import TestCase

from app.engine.recommender import Recommender
from app.models import DailyBar, StockInfo


class FakeDataSource:
    def __init__(self):
        self.trade_dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(100)]
        self.stocks = [
            StockInfo(symbol="000001", name="Alpha"),
            StockInfo(symbol="000002", name="Beta"),
        ]

    def get_stock_list(self):
        return self.stocks

    def get_trade_dates(self, start_date, end_date):
        return [d for d in self.trade_dates if start_date <= d <= end_date]

    def get_daily_bars(self, symbol, start_date, end_date):
        dates = [d for d in self.trade_dates if start_date <= d <= end_date]
        bars = []
        px = 10.0 if symbol == "000001" else 8.0
        drift = 1.004 if symbol == "000001" else 1.001
        for i, d in enumerate(dates):
            p = px * (drift**i)
            bars.append(
                DailyBar(
                    trade_date=d,
                    open=p * 0.99,
                    high=p * 1.01,
                    low=p * 0.98,
                    close=p,
                    volume=1_000_000,
                    turnover_rate=2.0,
                )
            )
        return bars

    def get_index_closes(self, symbol, start_date, end_date):
        dates = [d for d in self.trade_dates if start_date <= d <= end_date]
        return {d: 1000 + i for i, d in enumerate(dates)}


class TestRecommender(TestCase):
    def test_recommend_returns_one_stock(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
        }
        rec = Recommender(FakeDataSource(), cfg).recommend(date(2025, 3, 20))
        self.assertIn(rec.symbol, {"000001", "000002"})
        self.assertGreater(rec.score_total, 0.0)

