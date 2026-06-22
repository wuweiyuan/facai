from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.backtest.local_intraday_proxy import run_local_intraday_proxy_backtest
from app.main import build_parser


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _build_cache(root: Path) -> None:
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(90)]
    _write_csv(
        root / "meta" / "trade_calendar.csv",
        [{"trade_date": item.isoformat()} for item in dates],
    )
    _write_csv(
        root / "meta" / "stock_list.csv",
        [
            {"symbol": "000001", "name": "Alpha", "is_st": "false", "is_paused": "false", "market": "SZ"},
            {"symbol": "000002", "name": "Beta", "is_st": "false", "is_paused": "false", "market": "SZ"},
        ],
    )

    alpha_rows = []
    beta_rows = []
    alpha_close = 10.0
    beta_close = 10.0
    for idx, trade_date in enumerate(dates):
        if idx < 70:
            alpha_open = alpha_close * 1.001
            beta_open = beta_close * 1.001
            alpha_close *= 1.002
            beta_close *= 1.001
        elif idx == 70:
            alpha_prev = alpha_close
            beta_prev = beta_close
            alpha_open = alpha_prev * 1.02
            beta_open = beta_prev * 1.005
            alpha_close = alpha_prev * 1.03
            beta_close = beta_prev * 1.004
        elif idx == 71:
            alpha_open = alpha_close * 1.04
            beta_open = beta_close * 0.99
            alpha_close = alpha_open * 0.99
            beta_close = beta_open * 1.01
        else:
            alpha_open = alpha_close * 1.001
            beta_open = beta_close * 1.001
            alpha_close = alpha_open * 1.001
            beta_close = beta_open * 1.001
        alpha_rows.append(
            {
                "trade_date": trade_date.isoformat(),
                "open": f"{alpha_open:.6f}",
                "high": f"{max(alpha_open, alpha_close) * 1.01:.6f}",
                "low": f"{min(alpha_open, alpha_close) * 0.99:.6f}",
                "close": f"{alpha_close:.6f}",
                "volume": "1000000",
                "turnover_rate": "2.0",
            }
        )
        beta_rows.append(
            {
                "trade_date": trade_date.isoformat(),
                "open": f"{beta_open:.6f}",
                "high": f"{max(beta_open, beta_close) * 1.01:.6f}",
                "low": f"{min(beta_open, beta_close) * 0.99:.6f}",
                "close": f"{beta_close:.6f}",
                "volume": "1000000",
                "turnover_rate": "2.0",
            }
        )
    _write_csv(root / "bars" / "000001.csv", alpha_rows)
    _write_csv(root / "bars" / "000002.csv", beta_rows)


class TestIntradayProxyBacktest(TestCase):
    def test_auction_proxy_uses_open_entry_and_next_open_exit(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            _build_cache(cache_dir)
            cfg = {
                "data_source": {"cache_dir": str(cache_dir)},
                "execution_cost": {"enabled": False},
                "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
                "auction_pick": {
                    "count": 1,
                    "min_opening_gap": 0.012,
                    "max_opening_gap": 0.04,
                    "min_amount": 0,
                    "max_close_above_ma20_pct": 1.0,
                    "max_rsi14": 100,
                    "min_ma20_slope5": -1.0,
                },
            }

            summary = run_local_intraday_proxy_backtest(
                cfg,
                "auction-pick",
                date(2026, 3, 12),
                date(2026, 3, 13),
            )

            self.assertEqual(summary["strategy"], "auction-pick")
            self.assertEqual(summary["entry_price_desc"], "日线代理: 信号日开盘买入，次交易日开盘卖出")
            self.assertEqual(summary["total_trades"], 1)
            self.assertEqual(summary["records"][0]["symbol"], "000001")
            self.assertAlmostEqual(summary["avg_return_1d_net"], 0.0502, places=3)

    def test_tail_proxy_uses_close_entry_and_next_open_exit(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            _build_cache(cache_dir)
            cfg = {
                "data_source": {"cache_dir": str(cache_dir)},
                "execution_cost": {"enabled": False},
                "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
                "tail_pick": {
                    "min_intraday_return": 0.01,
                    "max_intraday_return": 0.06,
                    "min_amount": 0,
                    "min_latest_vs_open": 1.0,
                    "min_close_position": 0.5,
                    "max_fade_from_high": 1.0,
                    "max_close_above_ma20_pct": 1.0,
                    "max_rsi14": 100,
                    "min_ma20_slope5": -1.0,
                },
            }

            summary = run_local_intraday_proxy_backtest(
                cfg,
                "tail-pick",
                date(2026, 3, 12),
                date(2026, 3, 13),
            )

            self.assertEqual(summary["strategy"], "tail-pick")
            self.assertEqual(summary["entry_price_desc"], "日线代理: 信号日收盘买入，次交易日开盘卖出")
            self.assertEqual(summary["total_trades"], 1)
            self.assertEqual(summary["records"][0]["symbol"], "000001")
            self.assertAlmostEqual(summary["avg_return_1d_net"], 0.04, places=3)

    def test_tail_proxy_rejects_low_signal_day_volume(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            _build_cache(cache_dir)
            alpha_path = cache_dir / "bars" / "000001.csv"
            rows = list(csv.DictReader(alpha_path.open("r", encoding="utf-8", newline="")))
            rows[70]["volume"] = "500000"
            _write_csv(alpha_path, rows)
            cfg = {
                "data_source": {"cache_dir": str(cache_dir)},
                "execution_cost": {"enabled": False},
                "filters": {"exclude_st": True, "exclude_star_board": True, "exclude_bj_board": True},
                "tail_pick": {
                    "min_intraday_return": 0.01,
                    "max_intraday_return": 0.06,
                    "min_latest_vs_open": 1.0,
                    "min_close_position": 0.5,
                    "max_fade_from_high": 1.0,
                    "max_close_above_ma20_pct": 1.0,
                    "max_rsi14": 100,
                    "min_ma20_slope5": -1.0,
                    "min_intraday_volume_ratio_20": 1.2,
                },
            }

            summary = run_local_intraday_proxy_backtest(
                cfg,
                "tail-pick",
                date(2026, 3, 12),
                date(2026, 3, 13),
            )

            self.assertEqual(summary["total_trades"], 0)

    def test_parser_accepts_intraday_proxy_backtests(self):
        auction_args = build_parser().parse_args(
            ["backtest-auction-pick-proxy", "--start", "2026-03-12", "--end", "2026-03-14", "--output", "json"]
        )
        tail_args = build_parser().parse_args(
            ["backtest-tail-pick-proxy", "--start", "2026-03-12", "--end", "2026-03-14", "--output", "json"]
        )

        self.assertEqual(auction_args.cmd, "backtest-auction-pick-proxy")
        self.assertEqual(tail_args.cmd, "backtest-tail-pick-proxy")
