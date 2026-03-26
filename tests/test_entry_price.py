from __future__ import annotations

from datetime import date
from unittest import TestCase

import pandas as pd

from app.backtest.entry_price import add_signal_forward_returns, signal_attempted_dates


class TestEntryPrice(TestCase):
    def test_signal_attempted_dates_reserve_one_more_day_for_next_open(self):
        trade_dates = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8), date(2025, 1, 9), date(2025, 1, 10)]

        self.assertEqual(signal_attempted_dates(trade_dates, "close"), trade_dates[:-5])
        self.assertEqual(signal_attempted_dates(trade_dates, "next-open"), trade_dates[:-6])

    def test_add_signal_forward_returns_for_next_open(self):
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(
                    ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"]
                ),
                "open": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0],
                "close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5],
            }
        )

        enriched = add_signal_forward_returns(frame, "next-open")

        self.assertAlmostEqual(enriched.loc[0, "ret_fwd_1"], 12.5 / 11.0 - 1.0, places=8)
        self.assertAlmostEqual(enriched.loc[0, "ret_fwd_3"], 14.5 / 11.0 - 1.0, places=8)
        self.assertAlmostEqual(enriched.loc[0, "ret_fwd_5"], 16.5 / 11.0 - 1.0, places=8)
