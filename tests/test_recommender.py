from __future__ import annotations

from datetime import date, timedelta
import threading
import time
from unittest import TestCase

from app.config import apply_strategy_profile
from app.engine.recommender import Recommender
from app.main import (
    _resolve_dashboard_export_args,
    _resolve_recommend_run_specs,
    _resolve_recommend_target_date,
    build_parser,
)
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


class FakeStaleIndexDataSource(FakeDataSource):
    def get_index_closes(self, symbol, start_date, end_date):
        stale_end = end_date - timedelta(days=1)
        dates = [d for d in self.trade_dates if start_date <= d <= stale_end]
        return {d: 1000 + i for i, d in enumerate(dates)}


class FakeStaleStockDataSource(FakeDataSource):
    def get_daily_bars(self, symbol, start_date, end_date):
        return super().get_daily_bars(symbol, start_date, end_date - timedelta(days=1))


class FakeConcurrentDataSource(FakeDataSource):
    def __init__(self):
        super().__init__()
        self.stocks = [
            StockInfo(symbol="000001", name="Alpha"),
            StockInfo(symbol="000002", name="Beta"),
            StockInfo(symbol="000003", name="Gamma"),
            StockInfo(symbol="000004", name="Delta"),
        ]
        self._lock = threading.Lock()
        self._inflight = 0
        self.max_inflight = 0

    def get_daily_bars(self, symbol, start_date, end_date):
        with self._lock:
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        time.sleep(0.03)
        try:
            return super().get_daily_bars(symbol, start_date, end_date)
        finally:
            with self._lock:
                self._inflight -= 1


class FakePullbackDataSource(FakeDataSource):
    def __init__(self):
        super().__init__()
        self.trade_dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(140)]
        self.stocks = [
            StockInfo(symbol="000001", name="HotTrend"),
            StockInfo(symbol="000002", name="Pullback"),
        ]
        self._bars = {
            "000001": self._build_bars("000001", self._hot_returns()),
            "000002": self._build_bars("000002", self._pullback_returns()),
        }

    def _hot_returns(self):
        base = [0.003 if i % 6 else -0.002 for i in range(110)]
        tail = [
            0.012,
            0.010,
            0.009,
            -0.001,
            0.011,
            0.010,
            0.009,
            -0.002,
            0.010,
            0.009,
            0.008,
            0.007,
            0.009,
            0.008,
            0.010,
            -0.005,
            0.011,
            0.009,
            -0.006,
            0.010,
            0.008,
            -0.004,
            0.009,
            0.008,
            -0.003,
            0.009,
            0.008,
            0.009,
            0.007,
            0.008,
        ]
        return base + tail

    def _pullback_returns(self):
        base = [0.0032 if i % 7 else -0.0015 for i in range(110)]
        tail = [
            0.004,
            0.003,
            -0.004,
            -0.003,
            0.002,
            0.001,
            -0.002,
            0.003,
            0.002,
            -0.001,
            0.002,
            0.001,
            -0.002,
            0.003,
            0.002,
            -0.001,
            0.002,
            0.001,
            -0.001,
            0.002,
            0.002,
            -0.001,
            0.001,
            0.002,
            -0.001,
            0.001,
            0.002,
            -0.001,
            0.001,
            0.002,
        ]
        return base + tail

    def _build_bars(self, symbol, returns):
        bars = []
        close = 10.0 if symbol == "000001" else 9.5
        if len(self.trade_dates) != len(returns):
            raise ValueError("trade_dates and returns length must match")
        for idx, (trade_date, ret) in enumerate(zip(self.trade_dates, returns)):
            close *= 1.0 + ret
            volume = 1_000_000 + (idx % 9) * 25_000
            if symbol == "000001":
                volume += 80_000 + (idx % 5) * 20_000
            bars.append(
                DailyBar(
                    trade_date=trade_date,
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=volume,
                    turnover_rate=2.0,
                )
            )
        return bars

    def get_daily_bars(self, symbol, start_date, end_date):
        return [b for b in self._bars[symbol] if start_date <= b.trade_date <= end_date]


class TestRecommender(TestCase):
    def test_resolve_recommend_target_date_uses_next_trade_day_when_date_missing(self):
        ds = FakeDataSource()
        ds.trade_dates = [date(2025, 1, 10), date(2025, 1, 13), date(2025, 1, 14)]

        target = _resolve_recommend_target_date(ds, None, today=date(2025, 1, 10))

        self.assertEqual(target, date(2025, 1, 13))

    def test_resolve_recommend_target_date_keeps_explicit_date(self):
        ds = FakeDataSource()

        target = _resolve_recommend_target_date(ds, "2025-03-20", today=date(2025, 1, 10))

        self.assertEqual(target, date(2025, 3, 20))

    def test_resolve_recommend_run_specs_runs_default_and_pullback_for_recommend(self):
        specs = _resolve_recommend_run_specs("recommend")

        self.assertEqual(
            specs,
            [
                ("recommend", None, "默认策略 recommend"),
                ("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback"),
            ],
        )

    def test_resolve_recommend_run_specs_keeps_single_run_for_pullback_command(self):
        specs = _resolve_recommend_run_specs("recommend-pullback")

        self.assertEqual(
            specs,
            [("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback")],
        )

    def test_recommend_returns_one_stock(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
        }
        rec = Recommender(FakeDataSource(), cfg).recommend(date(2025, 3, 20))
        self.assertIn(rec.symbol, {"000001", "000002"})
        self.assertGreater(rec.score_total, 0.0)

    def test_recommend_many_honors_count(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"pick_count": 3, "weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
        }
        recs = Recommender(FakeDataSource(), cfg).recommend_many(date(2025, 3, 20))
        self.assertEqual(len(recs), 2)
        self.assertTrue(all(r.score_total > 0.0 for r in recs))

    def test_recommend_stops_when_index_is_stale(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
            "market_filter": {"enabled": True, "fail_on_error": True, "stop_on_stale": True},
        }
        with self.assertRaisesRegex(RuntimeError, "Market index stale"):
            Recommender(FakeStaleIndexDataSource(), cfg).recommend_many(date(2025, 3, 20))

    def test_recommend_stops_when_stock_is_stale(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {"weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2}},
            "data_freshness": {"enabled": False, "stop_on_stale_stock": True},
        }
        with self.assertRaisesRegex(RuntimeError, "Stock data stale"):
            Recommender(FakeStaleStockDataSource(), cfg).recommend_many(date(2025, 3, 20))

    def test_recommend_scans_symbols_concurrently_when_enabled(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {
                "scan_workers": 4,
                "weights": {"trend": 0.4, "momentum": 0.4, "stability": 0.2},
            },
            "data_freshness": {"enabled": False},
        }
        ds = FakeConcurrentDataSource()

        recs = Recommender(ds, cfg).recommend_many(date(2025, 3, 20))

        self.assertTrue(recs)
        self.assertGreaterEqual(ds.max_inflight, 2)

    def test_pullback_profile_prefers_near_ma20_stock(self):
        cfg = {
            "universe": {"limit": 100},
            "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
            "strategy": {
                "enabled_modes": ["force"],
                "pick_count": 1,
                "weights": {"trend": 0.35, "momentum": 0.35, "stability": 0.15, "volume": 0.15},
            },
            "data_freshness": {"enabled": False},
            "strategy_profiles": {
                "pullback_confirm": {
                    "strategy": {
                        "enabled_modes": ["normal"],
                        "weights": {
                            "trend": 0.20,
                            "momentum": 0.15,
                            "stability": 0.20,
                            "volume": 0.10,
                            "pullback": 0.35,
                        },
                    },
                    "risk_filter": {
                        "pullback": {
                            "enabled": True,
                            "min_close_above_ma20_pct": 0.0,
                            "max_close_above_ma20_pct": 0.035,
                            "min_mom20": 0.01,
                            "max_mom20": 0.18,
                            "min_mom5": -0.03,
                            "max_mom5": 0.04,
                            "min_rsi14": 42.0,
                            "max_rsi14": 75.0,
                            "max_volume_zscore20": 1.8,
                        }
                    },
                }
            },
        }
        ds = FakePullbackDataSource()

        default_rec = Recommender(ds, cfg).recommend(date(2025, 5, 20))
        pullback_cfg = apply_strategy_profile(cfg, "pullback_confirm")
        pullback_rec = Recommender(ds, pullback_cfg).recommend(date(2025, 5, 20))

        self.assertEqual(default_rec.symbol, "000001")
        self.assertEqual(pullback_rec.symbol, "000002")

    def test_apply_strategy_profile_does_not_mutate_base_config(self):
        cfg = {
            "strategy": {"weights": {"trend": 0.35, "momentum": 0.35}},
            "strategy_profiles": {"pullback_confirm": {"strategy": {"weights": {"pullback": 0.35}}}},
        }

        merged = apply_strategy_profile(cfg, "pullback_confirm")

        self.assertNotIn("pullback", cfg["strategy"]["weights"])
        self.assertEqual(merged["strategy"]["weights"]["pullback"], 0.35)

    def test_parser_accepts_recommend_pullback(self):
        args = build_parser().parse_args(["recommend-pullback", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-pullback")

    def test_parser_accepts_export_dashboard_data(self):
        args = build_parser().parse_args(["export-dashboard-data"])

        self.assertEqual(args.cmd, "export-dashboard-data")

    def test_resolve_dashboard_export_args_uses_default_and_pullback_paths(self):
        cfg = {
            "reporting": {
                "recommendation_csv": "reports/default.csv",
                "dashboard_data_js": "reports/dashboard.js",
            },
            "strategy_profiles": {
                "pullback_confirm": {
                    "reporting": {
                        "recommendation_csv": "reports/pullback.csv",
                    }
                }
            },
        }

        default_csv, pullback_csv, dashboard_js = _resolve_dashboard_export_args(cfg)

        self.assertEqual(default_csv, "reports/default.csv")
        self.assertEqual(pullback_csv, "reports/pullback.csv")
        self.assertEqual(dashboard_js, "reports/dashboard.js")
