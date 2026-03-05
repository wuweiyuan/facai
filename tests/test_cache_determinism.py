from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from app.data_source.akshare_client import AkshareDataSource
from app.models import DailyBar


class TestCacheDeterminism(TestCase):
    def _build_ds(self, root: str) -> AkshareDataSource:
        ds = AkshareDataSource.__new__(AkshareDataSource)
        ds.cache_enabled = True
        ds.cache_dir = Path(root)
        ds.bars_cache_dir = ds.cache_dir / "bars"
        ds.meta_cache_dir = ds.cache_dir / "meta"
        ds.index_cache_dir = ds.cache_dir / "index"
        ds.bars_cache_dir.mkdir(parents=True, exist_ok=True)
        ds.meta_cache_dir.mkdir(parents=True, exist_ok=True)
        ds.index_cache_dir.mkdir(parents=True, exist_ok=True)
        return ds

    def test_merge_save_bars_cache_keeps_existing_trade_date(self):
        with TemporaryDirectory() as tmp:
            ds = self._build_ds(tmp)
            symbol = "000001"
            path = ds._bars_cache_path(symbol)
            pd.DataFrame(
                {
                    "trade_date": ["2026-03-03"],
                    "open": [10.0],
                    "high": [10.2],
                    "low": [9.9],
                    "close": [10.1],
                    "volume": [1000.0],
                    "turnover_rate": [0.3],
                }
            ).to_csv(path, index=False)

            ds._merge_save_bars_cache(
                symbol,
                [
                    DailyBar(
                        trade_date=date(2026, 3, 3),
                        open=11.0,
                        high=11.2,
                        low=10.8,
                        close=11.1,
                        volume=2000.0,
                        turnover_rate=0.4,
                    ),
                    DailyBar(
                        trade_date=date(2026, 3, 4),
                        open=12.0,
                        high=12.2,
                        low=11.8,
                        close=12.1,
                        volume=3000.0,
                        turnover_rate=0.5,
                    ),
                ],
            )

            out = pd.read_csv(path)
            old_row = out[out["trade_date"] == "2026-03-03"].iloc[0]
            self.assertAlmostEqual(float(old_row["close"]), 10.1, places=8)
            self.assertEqual(len(out[out["trade_date"] == "2026-03-03"]), 1)
            self.assertEqual(len(out[out["trade_date"] == "2026-03-04"]), 1)
