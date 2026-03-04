from __future__ import annotations

from datetime import date
from unittest import TestCase

from app.backtest.runner import BacktestRunner
from app.engine.recommender import Recommender
from tests.test_recommender import FakeDataSource


class TestBacktest(TestCase):
    def test_backtest_summary(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
        }
        runner = BacktestRunner(Recommender(FakeDataSource(), cfg))
        summary = runner.run(date(2025, 1, 10), date(2025, 3, 10))
        self.assertIn("total_trades", summary)
        self.assertIn("win_rate_gross_1d", summary)
        self.assertIn("win_rate_gross_3d", summary)
        self.assertIn("win_rate_net_1d", summary)
        self.assertIn("win_rate_net_3d", summary)
        self.assertIn("avg_return_1d_net", summary)
        self.assertIn("avg_return_3d_net", summary)
        self.assertIn("attempted_days", summary)
        self.assertIn("skipped_days", summary)
        self.assertIn("error_counts", summary)
        self.assertIn("threshold_mode_counts", summary)
        self.assertGreaterEqual(summary["total_trades"], 1)

    def test_backtest_uses_multi_pick_portfolio(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"pick_count": 2, "weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
        }
        runner = BacktestRunner(Recommender(FakeDataSource(), cfg))
        summary = runner.run(date(2025, 1, 10), date(2025, 3, 10))
        self.assertGreaterEqual(summary["total_trades"], 1)
        self.assertTrue(any("+" in row["symbol"] for row in summary["records"]))

    def test_backtest_count_override(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"pick_count": 1, "weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
        }
        runner = BacktestRunner(Recommender(FakeDataSource(), cfg))
        summary = runner.run(date(2025, 1, 10), date(2025, 3, 10), count=2)
        self.assertGreaterEqual(summary["total_trades"], 1)
        self.assertTrue(any("+" in row["symbol"] for row in summary["records"]))
