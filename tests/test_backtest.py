from __future__ import annotations

from datetime import date
from unittest import TestCase

from app.backtest.runner import BacktestRunner
from app.config import apply_strategy_profile
from app.engine.recommender import Recommender
from app.main import build_parser
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

    def test_backtest_supports_next_open_entry_price(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"pick_count": 1, "weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
        }
        runner = BacktestRunner(Recommender(FakeDataSource(), cfg))

        close_summary = runner.run(date(2025, 1, 10), date(2025, 3, 10), entry_price_mode="close")
        next_open_summary = runner.run(date(2025, 1, 10), date(2025, 3, 10), entry_price_mode="next-open")

        self.assertEqual(close_summary["entry_price_mode"], "close")
        self.assertEqual(next_open_summary["entry_price_mode"], "next-open")
        self.assertEqual(next_open_summary["entry_price_desc"], "信号次日开盘买入")
        self.assertNotEqual(close_summary["avg_return_1d_gross"], next_open_summary["avg_return_1d_gross"])

    def test_backtest_pullback_profile_runs(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"pick_count": 1, "weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
            "strategy_profiles": {
                "pullback_confirm": {
                    "strategy": {"weights": {"trend": 0.2, "momentum": 0.2, "stability": 0.2, "volume": 0.1, "pullback": 0.3}}
                }
            },
        }
        profiled_cfg = apply_strategy_profile(cfg, "pullback_confirm")

        runner = BacktestRunner(Recommender(FakeDataSource(), profiled_cfg))
        summary = runner.run(date(2025, 1, 10), date(2025, 3, 10))

        self.assertGreaterEqual(summary["total_trades"], 1)
        self.assertIn("threshold_mode_counts", summary)

    def test_parser_accepts_backtest_pullback(self):
        args = build_parser().parse_args(
            ["backtest-pullback", "--start", "2025-01-10", "--end", "2025-03-10", "--entry-price", "next-open"]
        )

        self.assertEqual(args.cmd, "backtest-pullback")
        self.assertEqual(args.entry_price, "next-open")

    def test_parser_accepts_backtest_adaptive(self):
        args = build_parser().parse_args(
            ["backtest-adaptive", "--start", "2025-01-10", "--end", "2025-03-10", "--entry-price", "next-open"]
        )

        self.assertEqual(args.cmd, "backtest-adaptive")
        self.assertEqual(args.entry_price, "next-open")

    def test_parser_accepts_backtest_bull(self):
        args = build_parser().parse_args(
            ["backtest-bull", "--start", "2025-01-10", "--end", "2025-03-10", "--entry-price", "next-open"]
        )

        self.assertEqual(args.cmd, "backtest-bull")
        self.assertEqual(args.entry_price, "next-open")

    def test_parser_accepts_backtest_relative(self):
        args = build_parser().parse_args(
            ["backtest-relative", "--start", "2025-01-10", "--end", "2025-03-10", "--entry-price", "next-open"]
        )

        self.assertEqual(args.cmd, "backtest-relative")
        self.assertEqual(args.entry_price, "next-open")

    def test_parser_accepts_backtest_adaptive_rules(self):
        args = build_parser().parse_args(
            ["backtest-adaptive-rules", "--start", "2025-01-10", "--end", "2025-03-10", "--entry-price", "next-open"]
        )

        self.assertEqual(args.cmd, "backtest-adaptive-rules")
        self.assertEqual(args.entry_price, "next-open")
