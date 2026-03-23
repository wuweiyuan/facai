from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.dashboard import build_dashboard_payload, export_dashboard_data, normalize_symbol


class TestDashboard(TestCase):
    def test_normalize_symbol_zero_pads_numeric_values(self):
        self.assertEqual(normalize_symbol("1"), "000001")
        self.assertEqual(normalize_symbol("000001"), "000001")
        self.assertEqual(normalize_symbol("000001.SZ"), "000001")

    def test_build_dashboard_payload_deduplicates_by_trade_date_and_symbol(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            default_csv = base / "recommendations.csv"
            pullback_csv = base / "pullback_recommendations.csv"
            oversold_csv = base / "oversold_recommendations.csv"
            default_csv.write_text(
                "\n".join(
                    [
                        "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days",
                        "2026-03-17 09:00:00,2026-03-18,1,Alpha,normal,81.2,10.2,9.8,11.4,3",
                        "2026-03-17 10:30:00,2026-03-18,000001,Alpha,normal,82.6,10.3,9.9,11.6,3",
                        "2026-03-16 21:00:00,2026-03-17,000002,Beta,normal,75.0,8.1,7.8,9.0,2",
                    ]
                ),
                encoding="utf-8",
            )
            pullback_csv.write_text(
                "\n".join(
                    [
                        "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days",
                        "2026-03-17 20:00:00,2026-03-18,600000,Gamma,normal,66.6,12.0,11.2,13.5,1",
                    ]
                ),
                encoding="utf-8",
            )
            oversold_csv.write_text(
                "\n".join(
                    [
                        "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days",
                        "2026-03-17 20:10:00,2026-03-18,300001,Delta,normal,71.1,6.8,6.3,7.4,5",
                    ]
                ),
                encoding="utf-8",
            )

            cfg = {
                "data_source": {"cache_dir": str(base)},
                "market_filter": {"index_symbol": "000300", "lookback_days": 120},
                "adaptive_strategy": {
                    "regime_orders": {
                        "bull": ["recommend-pullback", "recommend"],
                        "neutral": ["recommend-pullback", "recommend"],
                        "bear": ["recommend-oversold"],
                        "unknown": ["recommend-pullback"],
                    }
                },
            }
            index_dir = base / "index"
            index_dir.mkdir(parents=True, exist_ok=True)
            (index_dir / "000300.csv").write_text(
                "\n".join(
                    [
                        "trade_date,close",
                        "2026-01-02,1000",
                        "2026-03-17,1010",
                        "2026-03-18,1020",
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_payload(default_csv, pullback_csv, oversold_csv, cfg)

            adaptive_records = payload["strategies"]["adaptive"]["records"]
            self.assertEqual(len(adaptive_records), 2)
            self.assertEqual(adaptive_records[0]["symbol"], "600000")
            self.assertEqual(adaptive_records[0]["source_strategy"], "recommend-pullback")
            self.assertEqual(payload["strategies"]["adaptive"]["available_dates"], ["2026-03-18", "2026-03-17"])
            self.assertEqual(payload["all_dates"], ["2026-03-18", "2026-03-17"])

    def test_build_dashboard_payload_handles_missing_files(self):
        with TemporaryDirectory() as tmp:
            payload = build_dashboard_payload(
                Path(tmp) / "missing-a.csv",
                Path(tmp) / "missing-b.csv",
                Path(tmp) / "missing-c.csv",
                {"data_source": {"cache_dir": str(Path(tmp))}},
            )

            self.assertEqual(payload["strategies"]["adaptive"]["records"], [])
            self.assertEqual(payload["all_dates"], [])

    def test_export_dashboard_data_writes_assignable_javascript(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            default_csv = base / "recommendations.csv"
            default_csv.write_text(
                "\n".join(
                    [
                        "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days",
                        "2026-03-17 10:30:00,2026-03-18,000001,Alpha,normal,82.6,10.3,9.9,11.6,3",
                    ]
                ),
                encoding="utf-8",
            )

            cfg = {"data_source": {"cache_dir": str(base)}}
            saved = export_dashboard_data(default_csv, base / "missing.csv", base / "missing-oversold.csv", base / "dashboard-data.js", cfg)

            content = saved.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("window.STOCK_DASHBOARD_DATA = "))
            self.assertIn('"label": "自适应策略"', content)
