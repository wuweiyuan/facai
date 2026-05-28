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
                "reporting": {"adaptive_run_csv": str(base / "adaptive_runs.csv")},
                "adaptive_strategy": {
                    "regime_orders": {
                        "bull": ["recommend-pullback", "recommend"],
                        "neutral": ["cash"],
                        "bear": ["recommend-oversold", "cash"],
                        "unknown": ["cash"],
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
            (base / "adaptive_runs.csv").write_text(
                "\n".join(
                    [
                        "run_time,target_date,signal_date,market_state,market_reason,tried_strategies,chosen_strategy,has_recommendations",
                        "2026-03-17 21:00:00,2026-03-17,2026-03-16,bear,ok,recommend-oversold,,false",
                        "2026-03-18 21:00:00,2026-03-18,2026-03-17,bull,ok,\"recommend-pullback,recommend\",recommend-pullback,true",
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_payload(default_csv, pullback_csv, oversold_csv, cfg)

            adaptive_records = payload["strategies"]["adaptive"]["records"]
            self.assertEqual(len(adaptive_records), 1)
            self.assertEqual(adaptive_records[0]["symbol"], "600000")
            self.assertEqual(adaptive_records[0]["source_strategy"], "recommend-pullback")
            self.assertEqual(payload["strategies"]["adaptive"]["available_dates"], ["2026-03-18", "2026-03-17"])
            self.assertFalse(payload["strategies"]["adaptive"]["date_summaries"]["2026-03-17"]["has_recommendations"])
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

    def test_cash_adaptive_day_keeps_opportunity_context(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            default_csv = base / "recommendations.csv"
            pullback_csv = base / "pullback_recommendations.csv"
            oversold_csv = base / "oversold_recommendations.csv"
            opportunity_csv = base / "opportunity_recommendations.csv"
            for path in [default_csv, pullback_csv, oversold_csv]:
                path.write_text(
                    "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days\n",
                    encoding="utf-8",
                )
            opportunity_csv.write_text(
                "\n".join(
                    [
                        "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days,exit_plan,source_strategy",
                        "2026-03-17 21:05:00,2026-03-18,600000,浦发银行,normal,70.5,9.2,8.9,9.9,2,默认持有2天；买后不强就退出。,recommend-pullback",
                    ]
                ),
                encoding="utf-8",
            )
            (base / "adaptive_runs.csv").write_text(
                "\n".join(
                    [
                        "run_time,target_date,signal_date,market_state,market_reason,tried_strategies,chosen_strategy,has_recommendations,chosen_count",
                        "2026-03-17 21:00:00,2026-03-18,2026-03-17,neutral,ok,cash,cash,true,3",
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_payload(
                default_csv,
                pullback_csv,
                oversold_csv,
                {
                    "data_source": {"cache_dir": str(base)},
                    "reporting": {"adaptive_run_csv": str(base / "adaptive_runs.csv")},
                    "adaptive_strategy": {"regime_orders": {"neutral": ["cash"], "unknown": ["cash"]}},
                },
                opportunity_csv=opportunity_csv,
            )

            summary = payload["strategies"]["adaptive"]["date_summaries"]["2026-03-18"]
            self.assertEqual(summary["formal_action"], "cash")
            self.assertFalse(summary["has_recommendations"])
            self.assertEqual(summary["chosen_count"], 0)
            self.assertTrue(summary["has_observation_candidates"])
            self.assertEqual(summary["opportunity_count"], 1)

    def test_cash_adaptive_day_uses_default_records_as_observation_candidates_when_opportunity_csv_missing(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            default_csv = base / "recommendations.csv"
            pullback_csv = base / "pullback_recommendations.csv"
            oversold_csv = base / "oversold_recommendations.csv"
            default_csv.write_text(
                "\n".join(
                    [
                        "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days,exit_plan",
                        "2026-05-28 09:29:15,2026-05-28,603725,天安新材,normal,91.58,12.2,11.12,14.34,3,默认持有3天；买后不强就退出。",
                        "2026-05-28 09:29:16,2026-05-28,003019,宸展光电,normal,88.72,44.17,40.74,51.01,3,默认持有3天；买后不强就退出。",
                    ]
                ),
                encoding="utf-8",
            )
            for path in [pullback_csv, oversold_csv]:
                path.write_text(
                    "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days\n",
                    encoding="utf-8",
                )
            (base / "adaptive_runs.csv").write_text(
                "\n".join(
                    [
                        "run_time,target_date,signal_date,market_state,market_reason,tried_strategies,chosen_strategy,has_recommendations,chosen_count",
                        "2026-05-28 09:29:16,2026-05-28,2026-05-27,neutral,ok,cash,cash,true,0",
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_payload(
                default_csv,
                pullback_csv,
                oversold_csv,
                {
                    "data_source": {"cache_dir": str(base)},
                    "reporting": {"adaptive_run_csv": str(base / "adaptive_runs.csv")},
                    "adaptive_strategy": {"regime_orders": {"neutral": ["cash"], "unknown": ["cash"]}},
                },
                opportunity_csv=base / "missing-opportunity.csv",
            )

            opportunity_records = payload["strategies"]["opportunity"]["records"]
            summary = payload["strategies"]["adaptive"]["date_summaries"]["2026-05-28"]
            self.assertEqual(summary["formal_action"], "cash")
            self.assertEqual(summary["opportunity_count"], 2)
            self.assertEqual([record["symbol"] for record in opportunity_records], ["603725", "003019"])
            self.assertEqual({record["source_strategy"] for record in opportunity_records}, {"recommend"})

    def test_build_dashboard_payload_normalizes_exit_plan_default_days(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            default_csv = base / "recommendations.csv"
            default_csv.write_text(
                "\n".join(
                    [
                        "run_time,trade_date,symbol,name,threshold_mode,score_total,close,stop_loss_price,take_profit_price,suggested_holding_days,exit_plan",
                        "2026-05-26 20:00:00,2026-05-27,000001,Alpha,normal,70.1,10.0,9.5,11.0,2,默认持有3天；买后1到2天不强或跌回MA20附近转弱就退出；放量冲高回落可分批止盈。",
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_payload(
                default_csv,
                base / "missing-pullback.csv",
                base / "missing-oversold.csv",
                {
                    "data_source": {"cache_dir": str(base)},
                    "adaptive_strategy": {"regime_orders": {"unknown": ["recommend"]}},
                },
            )

            record = payload["strategies"]["adaptive"]["records"][0]
            self.assertIn("默认持有2天", record["exit_plan"])
            self.assertNotIn("默认持有3天", record["exit_plan"])

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
