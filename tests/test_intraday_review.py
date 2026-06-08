from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.intraday_review import analyze_intraday_pick_signals
from app.main import build_parser
from app.models import DailyBar
from app.reporting import append_intraday_pick_signals


class FakeReviewDataSource:
    def get_trade_dates(self, start_date: date, end_date: date) -> list[date]:
        return [date(2026, 6, 4), date(2026, 6, 5), date(2026, 6, 8)]

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[DailyBar]:
        bars = {
            "000001": [
                DailyBar(date(2026, 6, 4), 9.8, 10.2, 9.7, 10.0, 1000),
                DailyBar(date(2026, 6, 5), 10.5, 10.8, 10.1, 10.2, 1200),
            ],
            "000002": [
                DailyBar(date(2026, 6, 4), 19.8, 20.5, 19.7, 20.0, 1000),
                DailyBar(date(2026, 6, 5), 19.0, 19.6, 18.8, 19.4, 1200),
            ],
        }
        return [bar for bar in bars.get(symbol, []) if start_date <= bar.trade_date <= end_date]


class TestIntradayReview(TestCase):
    def test_append_intraday_pick_signals_writes_selected_and_no_trade_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.jsonl"

            append_intraday_pick_signals(
                "tail-pick",
                {
                    "trade_date": "2026-06-04",
                    "selected": {
                        "symbol": "000001",
                        "name": "平安银行",
                        "entry_price": 10.0,
                        "score": 80.0,
                    },
                },
                str(path),
                source="unit-test",
            )
            append_intraday_pick_signals(
                "tail-pick",
                {"trade_date": "2026-06-05", "selected": None},
                str(path),
                source="unit-test",
            )

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["strategy"], "tail-pick")
            self.assertEqual(rows[0]["rank"], 1)
            self.assertEqual(rows[0]["symbol"], "000001")
            self.assertEqual(rows[0]["entry_price"], 10.0)
            self.assertTrue(rows[0]["selected"])
            self.assertFalse(rows[1]["selected"])
            self.assertEqual(rows[1]["entry_price"], None)

    def test_analyze_intraday_pick_signals_uses_next_open_as_primary_metric(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.jsonl"
            append_intraday_pick_signals(
                "auction-pick",
                {
                    "trade_date": "2026-06-04",
                    "selected": [
                        {"symbol": "000001", "name": "平安银行", "latest": 10.0, "score": 80.0},
                        {"symbol": "000002", "name": "万科A", "latest": 20.0, "score": 70.0},
                    ],
                },
                str(path),
                source="unit-test",
            )
            append_intraday_pick_signals(
                "auction-pick",
                {"trade_date": "2026-06-05", "selected": []},
                str(path),
                source="unit-test",
            )

            summary = analyze_intraday_pick_signals(str(path), FakeReviewDataSource(), strategy="auction-pick")

            self.assertEqual(summary["strategy"], "auction-pick")
            self.assertEqual(summary["signal_days"], 2)
            self.assertEqual(summary["no_trade_days"], 1)
            self.assertEqual(summary["selected_signals"], 2)
            self.assertEqual(summary["completed_trades"], 2)
            self.assertAlmostEqual(summary["win_rate_next_open"], 0.5)
            self.assertAlmostEqual(summary["avg_return_next_open"], 0.0)
            self.assertAlmostEqual(summary["median_return_next_open"], 0.0)
            self.assertAlmostEqual(summary["worst_return_next_open"], -0.05)
            self.assertAlmostEqual(summary["avg_return_next_close"], -0.005)
            self.assertEqual(summary["records"][0]["exit_date"], "2026-06-05")

    def test_parser_accepts_analyze_intraday_picks(self):
        args = build_parser().parse_args(
            [
                "analyze-intraday-picks",
                "--signals",
                "reports/intraday_pick_signals.jsonl",
                "--strategy",
                "tail-pick",
                "--output",
                "json",
            ]
        )

        self.assertEqual(args.cmd, "analyze-intraday-picks")
        self.assertEqual(args.strategy, "tail-pick")
