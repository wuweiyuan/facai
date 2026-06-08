from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta
from types import SimpleNamespace
import threading
import time
from unittest import TestCase
from unittest.mock import Mock, patch

import app.main as main_module
from app.config import apply_adaptive_parameter_overrides, apply_strategy_profile, load_config
from app.engine.recommender import Recommender
from app.main import (
    _resolve_adaptive_strategy_specs,
    _resolve_adaptive_pick_count,
    _resolve_dashboard_export_args,
    _resolve_opportunity_pool_specs,
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


class FakeOversoldDataSource(FakeDataSource):
    def __init__(self):
        super().__init__()
        self.trade_dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(140)]
        self.stocks = [
            StockInfo(symbol="000001", name="TrendLeader"),
            StockInfo(symbol="000002", name="OversoldBounce"),
        ]
        self._bars = {
            "000001": self._build_bars("000001", self._trend_returns()),
            "000002": self._build_bars("000002", self._oversold_returns()),
        }

    def _trend_returns(self):
        return [0.0025 if i % 6 else -0.001 for i in range(140)]

    def _oversold_returns(self):
        base = [0.0015 if i % 8 else -0.001 for i in range(110)]
        tail = [0.002, -0.001, 0.001, 0.0, -0.002] * 5 + [-0.032, -0.030, -0.028, -0.035, -0.050]
        return base + tail

    def _build_bars(self, symbol, returns):
        bars = []
        close = 11.0 if symbol == "000001" else 12.0
        for idx, (trade_date, ret) in enumerate(zip(self.trade_dates, returns)):
            close *= 1.0 + ret
            volume = 1_000_000 + (idx % 6) * 20_000
            if symbol == "000002" and idx >= len(self.trade_dates) - 5:
                volume = 1_900_000 + (idx - (len(self.trade_dates) - 5)) * 150_000
            bars.append(
                DailyBar(
                    trade_date=trade_date,
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=volume,
                    turnover_rate=2.5,
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
        specs = _resolve_recommend_run_specs("recommend-all")

        self.assertEqual(
            specs,
            [
                ("recommend", None, "默认策略 recommend"),
                ("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback"),
                ("recommend-oversold", "oversold_rebound", "超跌反弹策略 recommend-oversold"),
            ],
        )

    def test_resolve_recommend_run_specs_keeps_single_run_for_pullback_command(self):
        specs = _resolve_recommend_run_specs("recommend-pullback")

        self.assertEqual(
            specs,
            [("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback")],
        )

    def test_resolve_recommend_run_specs_keeps_single_run_for_recommend_command(self):
        specs = _resolve_recommend_run_specs("recommend")

        self.assertEqual(
            specs,
            [("recommend", None, "默认策略 recommend")],
        )

    def test_resolve_adaptive_strategy_specs_prefers_profile_order_from_config(self):
        cfg = {
            "adaptive_strategy": {
                "regime_orders": {
                    "bull": ["recommend", "recommend-pullback"],
                    "bear": ["recommend-oversold"],
                    "unknown": ["recommend-pullback"],
                }
            }
        }

        bull_specs = _resolve_adaptive_strategy_specs(cfg, "bull")
        bear_specs = _resolve_adaptive_strategy_specs(cfg, "bear")
        neutral_specs = _resolve_adaptive_strategy_specs(cfg, "neutral")

        self.assertEqual(
            bull_specs,
            [
                ("recommend", None, "默认策略 recommend"),
                ("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback"),
            ],
        )
        self.assertEqual(
            bear_specs,
            [("recommend-oversold", "oversold_rebound", "超跌反弹策略 recommend-oversold")],
        )
        self.assertEqual(
            neutral_specs,
            [("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback")],
        )

    def test_resolve_adaptive_strategy_specs_applies_profile_overrides(self):
        cfg = {
            "adaptive_strategy": {
                "regime_orders": {
                    "bull": ["recommend-pullback", "recommend"],
                },
                "profile_overrides": {
                    "recommend-pullback": "pullback_defensive",
                },
            }
        }

        specs = _resolve_adaptive_strategy_specs(cfg, "bull")

        self.assertEqual(
            specs,
            [
                ("recommend-pullback", "pullback_defensive", "回踩策略 recommend-pullback"),
                ("recommend", None, "默认策略 recommend"),
            ],
        )

    def test_resolve_adaptive_strategy_specs_stops_at_cash(self):
        cfg = {
            "adaptive_strategy": {
                "regime_orders": {
                    "neutral": ["cash", "recommend-pullback"],
                }
            }
        }

        specs = _resolve_adaptive_strategy_specs(cfg, "neutral")

        self.assertEqual(specs, [("cash", None, "空仓 cash")])

    def test_resolve_adaptive_pick_count_uses_strategy_defaults_and_allows_override(self):
        cfg = {
            "adaptive_strategy": {
                "strategy_pick_counts": {
                    "recommend": 1,
                    "recommend-pullback": 1,
                    "recommend-oversold": 3,
                }
            }
        }

        self.assertEqual(_resolve_adaptive_pick_count(cfg, "recommend", None), 1)
        self.assertEqual(_resolve_adaptive_pick_count(cfg, "recommend-oversold", None), 3)
        self.assertEqual(_resolve_adaptive_pick_count(cfg, "recommend-oversold", 2), 2)

    def test_resolve_opportunity_pool_specs_applies_profile_overrides(self):
        cfg = {
            "opportunity_pool": {
                "regime_orders": {
                    "bear": ["recommend-oversold", "recommend-pullback"],
                },
                "profile_overrides": {
                    "recommend-oversold": "oversold_opportunity",
                    "recommend-pullback": "pullback_opportunity",
                },
            }
        }

        specs = _resolve_opportunity_pool_specs(cfg, "bear")

        self.assertEqual(
            specs,
            [
                ("recommend-oversold", "oversold_opportunity", "超跌反弹策略 recommend-oversold"),
                ("recommend-pullback", "pullback_opportunity", "回踩策略 recommend-pullback"),
            ],
        )

    def test_recommend_adaptive_runs_opportunity_pool_when_no_signal(self):
        cfg = {"reporting": {"enabled": False}}
        fake_target_date = date(2025, 3, 20)
        fake_signal_date = date(2025, 3, 19)
        fake_market_state = SimpleNamespace(label="neutral")
        fake_opportunity_payload = {
            "target_date": "2025-03-20",
            "signal_date": "2025-03-19",
            "market_state": "neutral",
            "market_reason": "test reason",
            "pool": [
                {
                    "symbol": "000001",
                    "name": "Alpha",
                    "source_label": "回踩策略 recommend-pullback",
                    "score_total": 88.5,
                    "key_metrics": {"close": 12.3, "suggested_holding_days": 5},
                }
            ],
        }

        with (
            patch.object(sys, "argv", ["prog", "recommend-adaptive", "--output", "json"]),
            patch("app.main.load_config", return_value=cfg),
            patch("app.main._configure_network"),
            patch("app.main._build_data_source", return_value=Mock()),
            patch("app.main._resolve_recommend_target_date", return_value=fake_target_date),
            patch("app.main._resolve_adaptive_strategy_specs", return_value=[("recommend-pullback", "pullback_confirm", "x")]),
            patch("app.main._run_recommend_profile", side_effect=RuntimeError("No candidate found in enabled modes: normal")),
            patch("app.main._build_opportunity_pool", return_value=fake_opportunity_payload) as build_pool,
            patch("app.main.Recommender") as recommender_cls,
        ):
            recommender_cls.return_value.resolve_signal_date.return_value = fake_signal_date
            recommender_cls.return_value._resolve_market_state.return_value = (fake_market_state, "test reason")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main_module.main()

        payload = json.loads(buf.getvalue())
        self.assertIsNone(payload["chosen_strategy"])
        self.assertEqual(payload["recommendations"], [])
        self.assertEqual(payload["opportunity_pool"], fake_opportunity_payload)
        build_pool.assert_called_once()

    def test_recommend_adaptive_cash_skips_formal_recommend_and_builds_opportunity_pool(self):
        cfg = {"reporting": {"enabled": False}}
        fake_target_date = date(2025, 3, 20)
        fake_signal_date = date(2025, 3, 19)
        fake_market_state = SimpleNamespace(label="neutral")
        fake_opportunity_payload = {
            "target_date": "2025-03-20",
            "signal_date": "2025-03-19",
            "market_state": "neutral",
            "market_reason": "test reason",
            "pool": [{"symbol": "000001", "name": "Alpha"}],
        }

        with (
            patch.object(sys, "argv", ["prog", "recommend-adaptive", "--output", "json"]),
            patch("app.main.load_config", return_value=cfg),
            patch("app.main._configure_network"),
            patch("app.main._build_data_source", return_value=Mock()),
            patch("app.main._resolve_recommend_target_date", return_value=fake_target_date),
            patch("app.main._resolve_adaptive_strategy_specs", return_value=[("cash", None, "空仓 cash")]),
            patch("app.main._run_recommend_profile") as run_profile,
            patch("app.main._build_opportunity_pool", return_value=fake_opportunity_payload) as build_pool,
            patch("app.main.Recommender") as recommender_cls,
        ):
            recommender_cls.return_value.resolve_signal_date.return_value = fake_signal_date
            recommender_cls.return_value._resolve_market_state.return_value = (fake_market_state, "test reason")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main_module.main()

        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["chosen_strategy"], "cash")
        self.assertEqual(payload["chosen_count"], 0)
        self.assertEqual(payload["recommendations"], [])
        self.assertEqual(payload["opportunity_pool"], fake_opportunity_payload)
        run_profile.assert_not_called()
        build_pool.assert_called_once()

    def test_recommend_adaptive_does_not_run_opportunity_pool_when_signal_exists(self):
        cfg = {"reporting": {"enabled": False}}
        fake_target_date = date(2025, 3, 20)
        fake_signal_date = date(2025, 3, 19)
        fake_market_state = SimpleNamespace(label="neutral")
        fake_rec = Mock()
        fake_rec.as_dict.return_value = {"symbol": "000001", "name": "Alpha"}

        with (
            patch.object(sys, "argv", ["prog", "recommend-adaptive", "--output", "json"]),
            patch("app.main.load_config", return_value=cfg),
            patch("app.main._configure_network"),
            patch("app.main._build_data_source", return_value=Mock()),
            patch("app.main._resolve_recommend_target_date", return_value=fake_target_date),
            patch("app.main._resolve_adaptive_strategy_specs", return_value=[("recommend-pullback", "pullback_confirm", "x")]),
            patch("app.main._run_recommend_profile", return_value=([fake_rec], False)),
            patch("app.main._build_opportunity_pool") as build_pool,
            patch("app.main.Recommender") as recommender_cls,
        ):
            recommender_cls.return_value.resolve_signal_date.return_value = fake_signal_date
            recommender_cls.return_value._resolve_market_state.return_value = (fake_market_state, "test reason")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main_module.main()

        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["chosen_strategy"], "recommend-pullback")
        self.assertEqual(payload["recommendations"], [{"symbol": "000001", "name": "Alpha"}])
        self.assertIsNone(payload["opportunity_pool"])
        build_pool.assert_not_called()

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

    def test_oversold_profile_prefers_panic_selloff_stock(self):
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
                "oversold_rebound": {
                    "strategy": {
                        "threshold_profile": "oversold_rebound",
                        "enabled_modes": ["normal"],
                        "weights": {
                            "trend": 0.0,
                            "momentum": 0.0,
                            "stability": 0.15,
                            "volume": 0.20,
                            "oversold": 0.65,
                        },
                    },
                    "risk_filter": {
                        "oversold": {
                            "enabled": True,
                            "min_close_below_ma20_pct": 0.10,
                            "max_mom5": -0.12,
                            "max_ret_1d": -0.03,
                            "min_volume_ratio_1_20": 1.3,
                        }
                    },
                }
            },
        }
        ds = FakeOversoldDataSource()

        default_rec = Recommender(ds, cfg).recommend(date(2025, 5, 20))
        oversold_cfg = apply_strategy_profile(cfg, "oversold_rebound")
        oversold_rec = Recommender(ds, oversold_cfg).recommend(date(2025, 5, 20))

        self.assertEqual(default_rec.symbol, "000001")
        self.assertEqual(oversold_rec.symbol, "000002")
        self.assertEqual(int(oversold_rec.key_metrics["suggested_holding_days"]), 3)

    def test_apply_strategy_profile_does_not_mutate_base_config(self):
        cfg = {
            "strategy": {"weights": {"trend": 0.35, "momentum": 0.35}},
            "strategy_profiles": {"pullback_confirm": {"strategy": {"weights": {"pullback": 0.35}}}},
        }

        merged = apply_strategy_profile(cfg, "pullback_confirm")

        self.assertNotIn("pullback", cfg["strategy"]["weights"])
        self.assertEqual(merged["strategy"]["weights"]["pullback"], 0.35)

    def test_apply_adaptive_parameter_overrides_applies_market_command_override(self):
        cfg = {
            "strategy": {"pick_count": 1, "weights": {"trend": 0.20}},
            "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.05}},
            "adaptive_strategy": {
                "strategy_pick_counts": {"recommend-pullback": 1},
                "parameter_overrides": {
                    "bull": {
                        "recommend-pullback": {
                            "strategy": {"pick_count": 2, "weights": {"momentum": 0.15}},
                            "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.07}},
                        }
                    }
                },
            },
        }

        merged = apply_adaptive_parameter_overrides(cfg, "bull", "recommend-pullback")

        self.assertEqual(cfg["strategy"]["pick_count"], 1)
        self.assertNotIn("momentum", cfg["strategy"]["weights"])
        self.assertEqual(merged["strategy"]["pick_count"], 2)
        self.assertEqual(merged["strategy"]["weights"]["trend"], 0.20)
        self.assertEqual(merged["strategy"]["weights"]["momentum"], 0.15)
        self.assertEqual(merged["risk_filter"]["pullback"]["max_close_above_ma20_pct"], 0.07)
        self.assertEqual(merged["adaptive_strategy"]["strategy_pick_counts"]["recommend-pullback"], 2)

    def test_apply_adaptive_parameter_overrides_returns_copy_when_missing_or_invalid(self):
        cfg = {
            "strategy": {"pick_count": 1},
            "adaptive_strategy": {
                "parameter_overrides": {
                    "bull": {
                        "recommend-pullback": "invalid-block",
                    }
                }
            },
        }

        invalid = apply_adaptive_parameter_overrides(cfg, "bull", "recommend-pullback")
        missing = apply_adaptive_parameter_overrides(cfg, "bear", "recommend-oversold")

        self.assertEqual(invalid, cfg)
        self.assertEqual(missing, cfg)
        self.assertIsNot(invalid, cfg)
        self.assertIsNot(missing, cfg)

    def test_default_config_defines_pullback_defensive_profile(self):
        cfg = load_config("config/default.yaml")

        merged = apply_strategy_profile(cfg, "pullback_defensive")
        pullback_cfg = merged["risk_filter"]["pullback"]

        self.assertTrue(pullback_cfg["enabled"])
        self.assertEqual(pullback_cfg["max_volume_zscore20"], 1.0)
        self.assertEqual(pullback_cfg["min_ret_1d"], -0.04)
        self.assertEqual(merged["risk_filter"]["max_vol20_std"], 0.06)

    def test_default_config_promotes_stable_v2_adaptive_rules(self):
        cfg = load_config("config/default.yaml")

        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["bull"], ["recommend-pullback", "recommend"])
        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["neutral"], ["cash"])
        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["bear"], ["recommend-oversold", "cash"])
        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["unknown"], ["cash"])
        self.assertEqual(cfg["market_filter"]["bull_min_close_above_ma20_pct"], 0.01)
        self.assertEqual(cfg["market_filter"]["bull_min_mom20"], 0.04)

    def test_stable_v2_config_uses_cash_in_weak_regimes(self):
        cfg = load_config("config/default.stable-v2.yaml")

        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["bull"], ["recommend-pullback", "recommend"])
        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["neutral"], ["cash"])
        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["bear"], ["recommend-oversold", "cash"])
        self.assertEqual(cfg["adaptive_strategy"]["regime_orders"]["unknown"], ["cash"])
        self.assertEqual(cfg["market_filter"]["bull_min_close_above_ma20_pct"], 0.01)
        self.assertEqual(cfg["market_filter"]["bull_min_mom20"], 0.04)

    def test_parser_accepts_recommend_pullback(self):
        args = build_parser().parse_args(["recommend-pullback", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-pullback")

    def test_parser_accepts_recommend_oversold(self):
        args = build_parser().parse_args(["recommend-oversold", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-oversold")

    def test_parser_accepts_recommend_bull(self):
        args = build_parser().parse_args(["recommend-bull", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-bull")

    def test_parser_accepts_recommend_relative(self):
        args = build_parser().parse_args(["recommend-relative", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-relative")

    def test_parser_accepts_recommend_adaptive(self):
        args = build_parser().parse_args(["recommend-adaptive", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-adaptive")

    def test_parser_accepts_check_sector_map(self):
        args = build_parser().parse_args(["check-sector-map"])

        self.assertEqual(args.cmd, "check-sector-map")

    def test_parser_accepts_recommend_opportunity(self):
        args = build_parser().parse_args(["recommend-opportunity", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-opportunity")

    def test_parser_accepts_recommend_all(self):
        args = build_parser().parse_args(["recommend-all", "--date", "2025-03-20"])

        self.assertEqual(args.cmd, "recommend-all")

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
                },
                "oversold_rebound": {
                    "reporting": {
                        "recommendation_csv": "reports/oversold.csv",
                    }
                },
            },
        }

        default_csv, pullback_csv, oversold_csv, opportunity_csv, dashboard_js = _resolve_dashboard_export_args(cfg)

        self.assertEqual(default_csv, "reports/default.csv")
        self.assertEqual(pullback_csv, "reports/pullback.csv")
        self.assertEqual(oversold_csv, "reports/oversold.csv")
        self.assertEqual(opportunity_csv, "reports/opportunity_recommendations.csv")
        self.assertEqual(dashboard_js, "reports/dashboard.js")
