from __future__ import annotations

from datetime import date
from unittest import TestCase

from app.models import StockInfo
from app.universe.filtering import filter_universe


class TestUniverseFiltering(TestCase):
    def test_exclude_star_and_bj_extended_prefixes(self):
        stocks = [
            StockInfo(symbol="688001", name="STAR-A"),
            StockInfo(symbol="689009", name="STAR-B"),
            StockInfo(symbol="920001", name="BJ-A"),
            StockInfo(symbol="000001", name="MAIN"),
        ]
        cfg = {
            "filters": {
                "exclude_st": True,
                "exclude_star_board": True,
                "exclude_bj_board": True,
                "exclude_gem_board": False,
            }
        }
        out = filter_universe(stocks, cfg, date(2025, 1, 1))
        self.assertEqual([s.symbol for s in out], ["000001"])

    def test_exclude_gem_filters_300_and_301_prefixes(self):
        stocks = [
            StockInfo(symbol="300001", name="GEM-300"),
            StockInfo(symbol="301388", name="GEM-301"),
            StockInfo(symbol="600001", name="MAIN"),
        ]
        cfg = {
            "filters": {
                "exclude_st": True,
                "exclude_star_board": False,
                "exclude_bj_board": False,
                "exclude_gem_board": True,
            }
        }
        out = filter_universe(stocks, cfg, date(2025, 1, 1))
        self.assertEqual([s.symbol for s in out], ["600001"])
