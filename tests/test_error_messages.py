from __future__ import annotations

from unittest import TestCase

from app.error_messages import friendly_error_message


class TestErrorMessages(TestCase):
    def test_market_index_stale_message_contains_dates(self):
        msg = friendly_error_message(
            "Market index stale: symbol=000300, signal_date=2026-03-05, latest=2026-03-04"
        )
        self.assertIn("000300", msg)
        self.assertIn("2026-03-05", msg)
        self.assertIn("2026-03-04", msg)

    def test_stock_data_stale_message_contains_dates(self):
        msg = friendly_error_message("Stock data stale: symbol=000001, signal_date=2026-03-05, latest=2026-03-04")
        self.assertIn("000001", msg)
        self.assertIn("2026-03-05", msg)
        self.assertIn("2026-03-04", msg)
