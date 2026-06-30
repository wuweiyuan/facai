from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.config import load_config
from app.data_source.akshare_client import AkshareDataSource
from app.main import _print_tail_pick_payload, build_parser
from app.models import DailyBar, StockInfo
from app.tail_pick.automation import build_launchd_plist
from app.tail_pick.engine import TailPickEngine
from app.tail_pick.models import IntradayQuote, TailPickResult


def test_tail_pick_models_store_quote_and_result_fields():
    quote = IntradayQuote(
        symbol="000001",
        name="Ping An Bank",
        latest=10.5,
        previous_close=10.0,
        open=10.1,
        high=10.8,
        low=10.0,
        volume=1234567.0,
        amount=12800000.0,
        turnover_rate=2.3,
        snapshot_time=datetime(2026, 6, 4, 14, 45),
    )
    result = TailPickResult(
        trade_date=quote.snapshot_time.date(),
        quote=quote,
        score=82.5,
        entry_price=10.5,
        stop_loss_price=10.08,
        reasons=["tail-session gain is moderate", "amount passes threshold"],
    )

    assert result.quote.symbol == "000001"
    assert result.intraday_return == 0.05


def test_tail_pick_result_includes_next_day_sell_rules():
    quote = IntradayQuote(
        symbol="000001",
        name="Ping An Bank",
        latest=10.5,
        previous_close=10.0,
        open=10.1,
        high=10.8,
        low=10.0,
        volume=1234567.0,
        amount=12800000.0,
        turnover_rate=2.3,
        snapshot_time=datetime(2026, 6, 4, 14, 45),
    )
    result = TailPickResult(
        trade_date=quote.snapshot_time.date(),
        quote=quote,
        score=82.5,
        entry_price=10.5,
        stop_loss_price=10.08,
        reasons=["tail-session gain is moderate"],
    )

    payload = result.as_dict()

    assert payload["next_day_sell_rules"][0] == "跌破 10.08 立即卖出"
    assert "10:30" in payload["next_day_sell_rules"][4]


class FakeTailPickDataSource:
    def __init__(self):
        self.trade_dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(160)]
        self.stocks = [
            StockInfo(symbol="000001", name="Leader"),
            StockInfo(symbol="000002", name="TooHot"),
            StockInfo(symbol="000003", name="Weak"),
            StockInfo(symbol="000004", name="Second"),
        ]
        self.quotes = [
            IntradayQuote(
                "000001",
                "Leader",
                11.0,
                10.5,
                10.6,
                11.2,
                10.55,
                2_000_000,
                22_000_000,
                3.0,
                datetime(2026, 6, 4, 14, 45),
            ),
            IntradayQuote(
                "000002",
                "TooHot",
                12.0,
                10.5,
                10.8,
                12.1,
                10.7,
                2_500_000,
                30_000_000,
                4.0,
                datetime(2026, 6, 4, 14, 45),
            ),
            IntradayQuote(
                "000003",
                "Weak",
                8.1,
                8.0,
                8.0,
                8.2,
                7.9,
                1_000_000,
                8_000_000,
                1.0,
                datetime(2026, 6, 4, 14, 45),
            ),
            IntradayQuote(
                "000004",
                "Second",
                10.35,
                10.0,
                10.1,
                10.5,
                10.0,
                1_800_000,
                21_000_000,
                2.5,
                datetime(2026, 6, 4, 14, 45),
            ),
        ]
        self.daily_bar_symbols = []
        self.daily_bar_ranges = []
        self.daily_profile: dict[str, str] = {}
        self.daily_fail_symbols: set[str] = set()

    def get_stock_list(self):
        return self.stocks

    def get_trade_dates(self, start_date, end_date):
        return [d for d in self.trade_dates if start_date <= d <= end_date]

    def get_daily_bars(self, symbol, start_date, end_date):
        self.daily_bar_symbols.append(symbol)
        self.daily_bar_ranges.append((symbol, start_date, end_date))
        if symbol in self.daily_fail_symbols:
            raise OSError(11, "Resource deadlock avoided", f".cache/akshare/bars/{symbol}.csv")
        close = 10.0 if symbol != "000003" else 12.0
        bars = []
        dates = [d for d in self.trade_dates if start_date <= d <= end_date]
        profile = self.daily_profile.get(symbol, "weak" if symbol == "000003" else "normal")
        if profile == "recent_hot":
            close = 7.0
        for idx, trade_date in enumerate(dates):
            if profile == "weak":
                close *= 0.995
            elif profile == "overextended":
                close *= 1.001 if idx < max(len(dates) - 20, 0) else 1.025
            elif profile == "recent_hot":
                close *= 1.001 if idx < max(len(dates) - 5, 0) else 1.025
            else:
                close *= 1.002
            bars.append(
                DailyBar(
                    trade_date=trade_date,
                    open=close * 0.99,
                    high=close * 1.01,
                    low=close * 0.98,
                    close=close,
                    volume=1_000_000,
                    turnover_rate=2.0,
                )
            )
        return bars

    def get_intraday_quotes(self):
        return self.quotes


def test_tail_pick_selects_two_moderate_strength_candidates():
    engine = TailPickEngine(FakeTailPickDataSource(), {})

    payload = engine.pick(date(2026, 6, 4))

    assert [item.quote.symbol for item in payload.selected] == ["000004", "000001"]
    assert payload.selected[0].score > payload.selected[1].score
    assert payload.candidates_scanned == 4
    assert payload.candidates_passed == 2


def test_tail_pick_returns_no_trade_when_passed_candidates_below_minimum():
    engine = TailPickEngine(FakeTailPickDataSource(), {"tail_pick": {"min_required_candidates": 3}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected == []
    assert [item.quote.symbol for item in payload.observation_candidates] == ["000004", "000001"]
    assert payload.candidates_scanned == 4
    assert payload.candidates_passed == 2
    assert payload.filters["min_required_candidates"] == 3


def test_tail_pick_payload_serializes_observation_candidates_when_width_is_insufficient():
    engine = TailPickEngine(FakeTailPickDataSource(), {"tail_pick": {"min_required_candidates": 3}})

    payload = engine.pick(date(2026, 6, 4)).as_dict()

    assert payload["selected"] == []
    assert [item["symbol"] for item in payload["observation_candidates"]] == ["000004", "000001"]


def test_tail_pick_prints_observation_candidates_when_width_is_insufficient(capsys):
    engine = TailPickEngine(FakeTailPickDataSource(), {"tail_pick": {"min_required_candidates": 3}})
    payload = engine.pick(date(2026, 6, 4))

    _print_tail_pick_payload(payload, signal_path=None)

    captured = capsys.readouterr()
    assert "入围=2" in captured.out
    assert "低于最小宽度 3" in captured.out
    assert "[尾盘] 观察 1: 000004 Second" in captured.out


def test_tail_pick_default_config_uses_loose_c_calibration():
    cfg = load_config("config/default.yaml")
    tail_cfg = cfg["tail_pick"]

    assert tail_cfg["min_required_candidates"] == 3
    assert tail_cfg["min_intraday_return"] == 0.006
    assert tail_cfg["max_intraday_return"] == 0.075
    assert tail_cfg["min_turnover_rate"] == 0.8
    assert tail_cfg["max_turnover_rate"] == 16.0
    assert tail_cfg["min_intraday_volume_ratio_20"] == 0.9
    assert tail_cfg["max_current_return_3d"] == 0.16
    assert tail_cfg["max_current_return_5d"] == 0.24
    assert tail_cfg["min_score"] == 68
    assert tail_cfg["min_close_position"] == 0.58
    assert tail_cfg["max_fade_from_high"] == 0.04
    assert tail_cfg["amount_tiers"]["small_cap_min_amount"] == 40000000
    assert tail_cfg["amount_tiers"]["mid_cap_min_amount"] == 60000000
    assert tail_cfg["amount_tiers"]["large_cap_min_amount"] == 100000000


def test_tail_pick_payload_includes_filter_rejection_counts(capsys):
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Leader",
            9.9,
            10.5,
            10.6,
            10.7,
            9.8,
            2_000_000,
            20_000_000,
            3.0,
            datetime(2026, 6, 4, 14, 45),
        )
    ]

    payload = TailPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.as_dict()["filter_rejections"]["intraday_return"] == 1
    _print_tail_pick_payload(payload, signal_path=None)
    captured = capsys.readouterr()
    assert "筛选诊断" in captured.out
    assert "涨幅区间: 1" in captured.out


def test_tail_pick_returns_no_trade_when_all_quotes_fail():
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Leader",
            9.9,
            10.5,
            10.6,
            10.7,
            9.8,
            2_000_000,
            20_000_000,
            3.0,
            datetime(2026, 6, 4, 14, 45),
        )
    ]
    engine = TailPickEngine(ds, {})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_scanned == 1
    assert payload.candidates_passed == 0


def test_tail_pick_rejects_quote_not_above_open():
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "NotAboveOpen",
            10.90,
            10.50,
            11.00,
            11.10,
            10.50,
            2_000_000,
            22_000_000,
            3.0,
            datetime(2026, 6, 4, 14, 45),
        )
    ]

    payload = TailPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_tail_pick_rejects_late_fade_from_high():
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Fade",
            10.90,
            10.50,
            10.60,
            11.40,
            10.50,
            2_000_000,
            25_000_000,
            3.0,
            datetime(2026, 6, 4, 14, 45),
        )
    ]

    payload = TailPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_tail_pick_rejects_low_turnover_when_configured():
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "LowTurnover",
            10.90,
            10.50,
            10.60,
            11.00,
            10.50,
            2_000_000,
            100_000_000,
            0.4,
            datetime(2026, 6, 4, 14, 45),
        )
    ]

    payload = TailPickEngine(ds, {"tail_pick": {"min_turnover_rate": 1.5}}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_tail_pick_uses_market_cap_amount_tiers_when_available():
    ds = FakeTailPickDataSource()
    ds.stocks = [
        StockInfo(symbol="000001", name="SmallCap"),
        StockInfo(symbol="000004", name="LargeCap"),
    ]
    ds.quotes = [
        IntradayQuote(
            "000001",
            "SmallCap",
            10.40,
            10.00,
            10.10,
            10.50,
            10.00,
            6_000_000,
            60_000_000,
            2.5,
            datetime(2026, 6, 4, 14, 45),
            total_market_cap=4_000_000_000,
        ),
        IntradayQuote(
            "000004",
            "LargeCap",
            10.40,
            10.00,
            10.10,
            10.50,
            10.00,
            6_000_000,
            60_000_000,
            2.5,
            datetime(2026, 6, 4, 14, 45),
            total_market_cap=80_000_000_000,
        ),
    ]
    cfg = {
        "tail_pick": {
            "amount_tiers": {
                "enabled": True,
                "small_cap_max": 5_000_000_000,
                "mid_cap_max": 20_000_000_000,
                "small_cap_min_amount": 50_000_000,
                "mid_cap_min_amount": 80_000_000,
                "large_cap_min_amount": 120_000_000,
            }
        }
    }

    payload = TailPickEngine(ds, cfg).pick(date(2026, 6, 4))

    assert [item.quote.symbol for item in payload.selected] == ["000001"]
    assert ds.daily_bar_symbols == ["000001"]


def test_tail_pick_falls_back_to_price_amount_tiers_without_market_cap():
    ds = FakeTailPickDataSource()
    ds.stocks = [
        StockInfo(symbol="000001", name="LowPrice"),
        StockInfo(symbol="000004", name="HighPrice"),
    ]
    ds.quotes = [
        IntradayQuote(
            "000001",
            "LowPrice",
            8.20,
            8.00,
            8.05,
            8.30,
            8.00,
            7_500_000,
            60_000_000,
            2.5,
            datetime(2026, 6, 4, 14, 45),
        ),
        IntradayQuote(
            "000004",
            "HighPrice",
            80.00,
            76.00,
            77.00,
            81.00,
            76.50,
            750_000,
            60_000_000,
            2.5,
            datetime(2026, 6, 4, 14, 45),
        ),
    ]
    cfg = {
        "tail_pick": {
            "amount_tiers": {
                "enabled": True,
                "low_price_max": 10,
                "mid_price_max": 50,
                "low_price_min_amount": 50_000_000,
                "mid_price_min_amount": 80_000_000,
                "high_price_min_amount": 120_000_000,
            }
        }
    }

    payload = TailPickEngine(ds, cfg).pick(date(2026, 6, 4))

    assert [item.quote.symbol for item in payload.selected] == ["000001"]
    assert ds.daily_bar_symbols == ["000001"]


def test_tail_pick_rejects_intraday_volume_below_recent_average():
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "ThinVolume",
            10.90,
            10.50,
            10.60,
            11.00,
            10.50,
            500_000,
            100_000_000,
            2.0,
            datetime(2026, 6, 4, 14, 45),
        )
    ]

    payload = TailPickEngine(ds, {"tail_pick": {"min_intraday_volume_ratio_20": 1.2}}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_tail_pick_rejects_recently_overheated_candidate():
    ds = FakeTailPickDataSource()
    ds.daily_profile = {"000001": "recent_hot"}
    ds.quotes = [
        IntradayQuote(
            "000001",
            "RecentHot",
            10.90,
            10.50,
            10.60,
            11.00,
            10.50,
            2_000_000,
            100_000_000,
            2.0,
            datetime(2026, 6, 4, 14, 45),
        )
    ]

    payload = TailPickEngine(
        ds,
        {
            "tail_pick": {
                "max_current_return_5d": 0.08,
                "max_close_above_ma20_pct": 0.30,
            }
        },
    ).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_tail_pick_rejects_candidate_below_minimum_score():
    ds = FakeTailPickDataSource()

    payload = TailPickEngine(ds, {"tail_pick": {"min_score": 99}}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_tail_pick_rejects_overextended_daily_candidate():
    ds = FakeTailPickDataSource()
    ds.daily_profile = {"000001": "overextended"}
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Overextended",
            11.00,
            10.50,
            10.60,
            11.10,
            10.55,
            2_000_000,
            25_000_000,
            3.0,
            datetime(2026, 6, 4, 14, 45),
        )
    ]

    payload = TailPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_tail_pick_skips_symbol_when_daily_bars_fail_and_warns(capsys):
    ds = FakeTailPickDataSource()
    ds.daily_fail_symbols = {"000001", "000004"}

    payload = TailPickEngine(ds, {}).pick(date(2026, 6, 4))

    captured = capsys.readouterr()
    assert payload.selected == []
    assert payload.candidates_passed == 0
    assert "跳过 000001 Leader" in captured.err
    assert "Resource deadlock avoided" in captured.err


def test_tail_pick_prefilters_quotes_before_fetching_daily_bars():
    ds = FakeTailPickDataSource()
    engine = TailPickEngine(ds, {"tail_pick": {"max_snapshot_candidates": 1}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected
    assert payload.selected[0].quote.symbol == "000001"
    assert ds.daily_bar_symbols == ["000001"]


def test_tail_pick_caps_prefiltered_snapshot_candidates_by_amount():
    ds = FakeTailPickDataSource()
    ds.stocks.append(StockInfo(symbol="000004", name="SecondValid"))
    ds.quotes.append(
        IntradayQuote(
            "000004",
            "SecondValid",
            10.4,
            10.0,
            10.1,
            10.5,
            10.0,
            2_000_000,
            21_000_000,
            2.8,
            datetime(2026, 6, 4, 14, 45),
        )
    )
    engine = TailPickEngine(ds, {"tail_pick": {"max_snapshot_candidates": 1}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected
    assert ds.daily_bar_symbols == ["000001"]


def test_tail_pick_uses_previous_trade_date_for_daily_trend():
    ds = FakeTailPickDataSource()
    engine = TailPickEngine(ds, {"tail_pick": {"max_snapshot_candidates": 1}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected
    assert ds.daily_bar_ranges[0][2] == date(2026, 6, 3)


def test_akshare_spot_rows_normalize_to_intraday_quotes():
    df = pd.DataFrame(
        [
            {
                "代码": "1",
                "名称": "Ping An Bank",
                "最新价": 10.5,
                "昨收": 10.0,
                "今开": 10.1,
                "最高": 10.8,
                "最低": 10.0,
                "成交量": 1234567,
                "成交额": 12800000,
                "换手率": 2.3,
                "总市值": 12300000000,
                "流通市值": 9800000000,
            }
        ]
    )

    quotes = AkshareDataSource._spot_rows_to_intraday_quotes(df)

    assert quotes[0].symbol == "000001"
    assert quotes[0].intraday_return == 0.05
    assert quotes[0].total_market_cap == 12300000000
    assert quotes[0].float_market_cap == 9800000000


def test_akshare_spot_rows_strip_market_prefix_from_symbol():
    df = pd.DataFrame(
        [
            {
                "代码": "sz000001",
                "名称": "Ping An Bank",
                "最新价": 10.5,
                "昨收": 10.0,
                "今开": 10.1,
                "最高": 10.8,
                "最低": 10.0,
                "成交量": 1234567,
                "成交额": 12800000,
            }
        ]
    )

    quotes = AkshareDataSource._spot_rows_to_intraday_quotes(df)

    assert quotes[0].symbol == "000001"


def test_tail_pick_parser_accepts_output_and_date():
    args = build_parser().parse_args(["tail-pick", "--date", "2026-06-04", "--output", "json"])

    assert args.cmd == "tail-pick"
    assert args.date == "2026-06-04"
    assert args.output == "json"


def test_tail_pick_launchd_plist_runs_weekdays_at_1444():
    plist = build_launchd_plist("/tmp/project", python_bin="/usr/bin/python3")

    assert plist["Label"] == "com.wayne.tail-pick"
    assert plist["WorkingDirectory"] == "/tmp/project"
    assert plist["ProgramArguments"] == ["/bin/bash", "/tmp/project/scripts/run_tail_pick_auto.sh"]
    assert plist["StartCalendarInterval"] == [
        {"Weekday": 1, "Hour": 14, "Minute": 44},
        {"Weekday": 2, "Hour": 14, "Minute": 44},
        {"Weekday": 3, "Hour": 14, "Minute": 44},
        {"Weekday": 4, "Hour": 14, "Minute": 44},
        {"Weekday": 5, "Hour": 14, "Minute": 44},
    ]
    assert plist["EnvironmentVariables"]["PYTHON_BIN"] == "/usr/bin/python3"


def test_tail_pick_auto_runner_notifies_and_opens_latest_log():
    script = Path("scripts/run_tail_pick_auto.sh").read_text(encoding="utf-8")

    assert "TAIL_STATUS=0" in script
    assert "TAIL_STATUS=$?" in script
    assert "app.trading_calendar" in script
    assert "not an A-share trading day; skip tail-pick" in script
    assert "osascript -e" in script
    assert "display notification" in script
    assert 'open "${LATEST_LOG}"' in script
