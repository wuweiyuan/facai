from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from app.auction_pick.engine import AuctionPickEngine
from app.auction_pick.models import AuctionPickResult
from app.config import load_config
from app.main import build_parser
from app.models import DailyBar, StockInfo
from app.tail_pick.models import IntradayQuote


class FakeAuctionDataSource:
    def __init__(self):
        self.trade_dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(180)]
        self.stocks = [
            StockInfo(symbol="000001", name="Leader"),
            StockInfo(symbol="000002", name="TooHot"),
            StockInfo(symbol="000003", name="Fade"),
            StockInfo(symbol="000004", name="WeakTrend"),
            StockInfo(symbol="000005", name="Second"),
        ]
        self.quotes = [
            IntradayQuote(
                "000001",
                "Leader",
                10.35,
                10.00,
                10.20,
                10.40,
                10.18,
                2_000_000,
                25_000_000,
                2.1,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000002",
                "TooHot",
                10.85,
                10.00,
                10.60,
                10.90,
                10.55,
                3_000_000,
                35_000_000,
                3.2,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000003",
                "Fade",
                10.10,
                10.00,
                10.20,
                10.25,
                10.05,
                2_500_000,
                26_000_000,
                2.8,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000004",
                "WeakTrend",
                10.30,
                10.00,
                10.15,
                10.35,
                10.10,
                2_000_000,
                24_000_000,
                2.0,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000005",
                "Second",
                10.28,
                10.00,
                10.12,
                10.32,
                10.10,
                2_000_000,
                20_000_000,
                1.8,
                datetime(2026, 6, 4, 9, 26),
            ),
        ]
        self.daily_bar_symbols: list[str] = []
        self.daily_bar_ranges: list[tuple[str, date, date]] = []
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
        dates = [d for d in self.trade_dates if start_date <= d <= end_date]
        close = 10.0
        bars = []
        profile = self.daily_profile.get(symbol, "weak" if symbol == "000004" else "normal")
        for idx, trade_date in enumerate(dates):
            if profile == "weak":
                close *= 0.995
            elif profile == "overextended":
                close *= 1.001 if idx < max(len(dates) - 20, 0) else 1.025
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


def test_auction_pick_result_serializes_core_fields():
    quote = IntradayQuote(
        "000001",
        "Leader",
        10.35,
        10.00,
        10.20,
        10.40,
        10.18,
        2_000_000,
        25_000_000,
        2.1,
        datetime(2026, 6, 4, 9, 26),
    )
    result = AuctionPickResult(
        trade_date=date(2026, 6, 4),
        quote=quote,
        score=81.5,
        opening_gap=0.02,
        current_return=0.035,
        reasons=["opening gap 2.00%", "amount 2500w"],
    )

    payload = result.as_dict()

    assert payload["symbol"] == "000001"
    assert payload["opening_gap"] == 0.02
    assert payload["current_return"] == 0.035
    assert payload["execution_notes"][0] == "9:30-9:35 不破开盘价和分时均价线再考虑试仓"


def test_auction_pick_selects_ranked_candidates_and_skips_failed_filters():
    engine = AuctionPickEngine(FakeAuctionDataSource(), {"auction_pick": {"count": 2}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.candidates_scanned == 5
    assert payload.candidates_passed == 2
    assert [item.quote.symbol for item in payload.selected] == ["000001", "000005"]
    assert payload.selected[0].score > payload.selected[1].score


def test_auction_pick_prefilters_snapshot_before_fetching_daily_bars():
    ds = FakeAuctionDataSource()
    engine = AuctionPickEngine(ds, {})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected
    assert ds.daily_bar_symbols == ["000001", "000004", "000005"]


def test_auction_pick_uses_previous_trade_date_for_daily_trend():
    ds = FakeAuctionDataSource()
    engine = AuctionPickEngine(ds, {"auction_pick": {"count": 1}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected
    assert ds.daily_bar_ranges[0][2] == date(2026, 6, 3)


def test_auction_pick_returns_no_trade_when_all_quotes_fail():
    ds = FakeAuctionDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Leader",
            10.05,
            10.00,
            10.04,
            10.08,
            10.00,
            1_000_000,
            9_000_000,
            1.0,
            datetime(2026, 6, 4, 9, 26),
        )
    ]
    engine = AuctionPickEngine(ds, {})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_scanned == 1
    assert payload.candidates_passed == 0


def test_auction_pick_rejects_current_price_below_open():
    ds = FakeAuctionDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Fade",
            10.19,
            10.00,
            10.20,
            10.25,
            10.05,
            2_000_000,
            25_000_000,
            2.0,
            datetime(2026, 6, 4, 9, 26),
        )
    ]

    payload = AuctionPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_auction_pick_rejects_overextended_daily_candidate():
    ds = FakeAuctionDataSource()
    ds.daily_profile = {"000001": "overextended"}
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Overextended",
            10.30,
            10.00,
            10.20,
            10.35,
            10.18,
            2_000_000,
            25_000_000,
            2.0,
            datetime(2026, 6, 4, 9, 26),
        )
    ]

    payload = AuctionPickEngine(ds, {}).pick(date(2026, 6, 4), count=1)

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_auction_pick_skips_symbol_when_daily_bars_fail_and_warns(capsys):
    ds = FakeAuctionDataSource()
    ds.daily_fail_symbols = {"000001"}
    ds.quotes = [
        ds.quotes[0],
        ds.quotes[4],
    ]

    payload = AuctionPickEngine(ds, {}).pick(date(2026, 6, 4), count=2)

    captured = capsys.readouterr()
    assert [item.quote.symbol for item in payload.selected] == ["000005"]
    assert payload.candidates_passed == 1
    assert "跳过 000001 Leader" in captured.err
    assert "Resource deadlock avoided" in captured.err


def test_auction_pick_parser_accepts_date_count_and_output():
    args = build_parser().parse_args(["auction-pick", "--date", "2026-06-04", "--count", "3", "--output", "json"])

    assert args.cmd == "auction-pick"
    assert args.date == "2026-06-04"
    assert args.count == 3
    assert args.output == "json"


def test_default_config_contains_isolated_auction_pick_section():
    cfg = load_config("config/default.yaml")

    assert cfg["auction_pick"]["count"] == 2
    assert cfg["auction_pick"]["min_opening_gap"] == 0.012
    assert cfg["auction_pick"]["max_opening_gap"] == 0.04
    assert cfg["auction_pick"]["min_current_return"] == 0.012
    assert cfg["auction_pick"]["max_current_return"] == 0.055
    assert cfg["auction_pick"]["min_amount"] == 20_000_000
    assert cfg["auction_pick"]["min_latest_vs_open"] == 1.0
    assert cfg["auction_pick"]["limit_up_return"] == 0.09
    assert cfg["auction_pick"]["max_close_above_ma20_pct"] == 0.08
    assert cfg["auction_pick"]["max_rsi14"] == 75
    assert cfg["auction_pick"]["min_ma20_slope5"] == 0.0


def test_auction_pick_does_not_change_existing_parser_commands():
    parser = build_parser()

    tail_args = parser.parse_args(["tail-pick", "--date", "2026-06-04", "--output", "json"])
    adaptive_args = parser.parse_args(["recommend-adaptive", "--date", "2026-06-04", "--count", "1"])

    assert tail_args.cmd == "tail-pick"
    assert adaptive_args.cmd == "recommend-adaptive"
    assert adaptive_args.count == 1


def test_auction_pick_launchd_plist_runs_weekdays_at_0926():
    from app.auction_pick.automation import build_launchd_plist

    plist = build_launchd_plist("/tmp/project", python_bin="/usr/bin/python3")

    assert plist["Label"] == "com.wayne.auction-pick"
    assert plist["WorkingDirectory"] == "/tmp/project"
    assert plist["ProgramArguments"] == ["/bin/bash", "/tmp/project/scripts/run_auction_pick_auto.sh"]
    assert plist["StartCalendarInterval"] == [
        {"Weekday": 1, "Hour": 9, "Minute": 26},
        {"Weekday": 2, "Hour": 9, "Minute": 26},
        {"Weekday": 3, "Hour": 9, "Minute": 26},
        {"Weekday": 4, "Hour": 9, "Minute": 26},
        {"Weekday": 5, "Hour": 9, "Minute": 26},
    ]
    assert plist["EnvironmentVariables"]["PYTHON_BIN"] == "/usr/bin/python3"
    assert plist["EnvironmentVariables"]["AUCTION_COUNT"] == "2"
    assert plist["StandardOutPath"] == "/tmp/project/reports/auction_pick/launchd.out.log"
    assert plist["StandardErrorPath"] == "/tmp/project/reports/auction_pick/launchd.err.log"


def test_auction_pick_auto_runner_notifies_and_opens_latest_log():
    script = Path("scripts/run_auction_pick_auto.sh").read_text(encoding="utf-8")

    assert "AUCTION_STATUS=0" in script
    assert "AUCTION_STATUS=$?" in script
    assert "auction-pick" in script
    assert 'REPORT_DIR="${PROJECT_ROOT}/reports/auction_pick"' in script
    assert "display notification" in script
    assert 'open "${LATEST_LOG}"' in script


def test_auction_pick_launchd_install_and_uninstall_scripts_use_distinct_label():
    install_script = Path("scripts/install_auction_pick_launchd.sh").read_text(encoding="utf-8")
    uninstall_script = Path("scripts/uninstall_auction_pick_launchd.sh").read_text(encoding="utf-8")

    assert 'LABEL="com.wayne.auction-pick"' in install_script
    assert 'LABEL="com.wayne.auction-pick"' in uninstall_script
    assert "app.auction_pick.automation" in install_script
    assert "--hour 9" in install_script
    assert "--minute 26" in install_script
    assert "reports/auction_pick" in install_script
