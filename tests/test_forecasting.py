from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import numpy as np
import pandas as pd

from app.forecasting.features import (
    FEATURE_COLUMNS,
    build_training_frame,
    fit_ridge,
    predict_ridge,
    select_horizon_model,
)
from app.forecasting.engine import ForecastEngine
from app.forecasting.models import ForecastBatch, HorizonForecast, StockForecast
from app.forecasting.reporting import batch_to_dict, write_forecast_csv
from app.main import build_parser


def _bars(count: int = 90, drift: float = 0.0) -> pd.DataFrame:
    rows = []
    close = 10.0
    start = date(2025, 1, 1)
    for offset in range(count):
        close *= (1.006 + drift) if offset % 4 in (0, 1) else (0.995 + drift)
        rows.append(
            {
                "trade_date": start + timedelta(days=offset),
                "open": close * 0.995,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000 + offset * 1_000,
                "turnover_rate": 1.5,
            }
        )
    return pd.DataFrame(rows)


class TestForecastFeatures(TestCase):
    def test_training_frame_has_forward_label_but_not_future_feature(self):
        frame = build_training_frame(_bars(), horizon=5)
        row = frame.iloc[0]
        self.assertAlmostEqual(row["target_return"], row["close_t_plus_h"] / row["close"] - 1.0)
        self.assertNotIn("close_t_plus_h", FEATURE_COLUMNS)

    def test_ridge_fit_returns_finite_coefficients(self):
        model = fit_ridge(
            np.array([[0.0], [1.0], [2.0], [3.0]]),
            np.array([0.0, 1.0, 2.0, 3.0]),
            alpha=1.0,
        )
        self.assertTrue(np.isfinite(model.coef_).all())
        self.assertGreater(predict_ridge(model, np.array([[4.0]]))[0], 2.0)

    def test_training_frame_keeps_rows_when_turnover_rate_is_unavailable(self):
        bars = _bars()
        bars["turnover_rate"] = np.nan
        frame = build_training_frame(bars, horizon=5)
        self.assertFalse(frame.empty)
        self.assertTrue((frame["turnover_rate"] == 0.0).all())


class TestForecastSelection(TestCase):
    def test_select_horizon_model_uses_chronological_validation(self):
        frame = build_training_frame(_bars(150), horizon=5)
        selected = select_horizon_model(
            frame,
            horizon=5,
            candidate_windows=(20, 30),
            candidate_alphas=(0.1, 10.0),
            validation_samples=12,
        )
        self.assertIn(selected.train_window, {20, 30})
        self.assertIn(selected.alpha, {0.1, 10.0})
        self.assertEqual(len(selected.validation_residuals), 12)

    def test_select_horizon_model_rejects_insufficient_history(self):
        frame = build_training_frame(_bars(90), horizon=5).iloc[:30]
        with self.assertRaisesRegex(ValueError, "insufficient training samples"):
            select_horizon_model(frame, 5, (40,), (1.0,), validation_samples=10)


class TestForecastEngine(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_cache(self, frames: dict[str, pd.DataFrame]) -> None:
        bars_dir = self.root / "bars"
        meta_dir = self.root / "meta"
        bars_dir.mkdir(parents=True)
        meta_dir.mkdir()
        pd.DataFrame(
            [{"symbol": symbol, "name": f"股票{symbol}", "is_st": False, "is_paused": False, "market": "SZ"} for symbol in frames]
        ).to_csv(meta_dir / "stock_list.csv", index=False)
        for symbol, frame in frames.items():
            frame.to_csv(bars_dir / f"{symbol}.csv", index=False)

    def _config(self) -> dict:
        return {
            "data_source": {"cache_dir": str(self.root)},
            "forecasting": {
                "min_history_bars": 120,
                "min_train_samples": 30,
                "validation_samples": 20,
                "candidate_train_windows": [30, 60],
                "ridge_alphas": [0.1, 1.0],
            },
        }

    def test_engine_trains_each_eligible_stock_and_ranks_by_expected_5d_return(self):
        self._write_cache({"000001": _bars(330, drift=0.003), "000002": _bars(330, drift=-0.003)})
        batch = ForecastEngine(self._config()).rank()
        self.assertEqual(len(batch.items), 2)
        self.assertGreaterEqual(batch.items[0].forecast_5d.expected_return, batch.items[1].forecast_5d.expected_return)
        self.assertEqual(batch.items[0].signal_date, batch.source_last_bar_date)

    def test_engine_skips_stale_and_short_history_without_stopping_batch(self):
        self._write_cache({"000001": _bars(330), "000003": _bars(100), "000004": _bars(300)})
        batch = ForecastEngine(self._config()).rank()
        self.assertEqual([item.symbol for item in batch.items], ["000001"])
        self.assertEqual(batch.skipped["insufficient_history"], 1)
        self.assertEqual(batch.skipped["stale_bar"], 1)


def _forecast_batch() -> ForecastBatch:
    horizon_5 = HorizonForecast(5, 0.03, 0.65, 60, 1.0, 0.02, 0.55, (0.01, -0.02))
    horizon_10 = HorizonForecast(10, 0.05, 0.60, 60, 1.0, 0.03, 0.52, (0.02, -0.03))
    item = StockForecast("000001", "平安银行", date(2026, 7, 13), date(2026, 7, 13), 330, horizon_5, horizon_10)
    return ForecastBatch(date(2026, 7, 13), date(2026, 7, 13), (item,), {"stale_bar": 2})


class TestForecastReporting(TestCase):
    def test_forecast_csv_and_json_include_model_metadata(self):
        with TemporaryDirectory() as tmp:
            saved = write_forecast_csv(_forecast_batch(), Path(tmp) / "forecast_rank.csv")
            columns = pd.read_csv(saved).columns.tolist()
        self.assertEqual(columns[:4], ["rank", "symbol", "name", "signal_date"])
        self.assertIn("expected_return_5d", columns)
        self.assertIn("ridge_alpha_10d", columns)
        payload = batch_to_dict(_forecast_batch(), limit=1)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["summary"]["eligible_count"], 1)


class TestForecastCli(TestCase):
    def test_forecast_rank_parser_accepts_output_count_date_and_no_save(self):
        args = build_parser().parse_args(
            ["forecast-rank", "--date", "2026-07-13", "--count", "7", "--output", "json", "--no-save"]
        )
        self.assertEqual((args.cmd, args.count, args.output, args.no_save), ("forecast-rank", 7, "json", True))
