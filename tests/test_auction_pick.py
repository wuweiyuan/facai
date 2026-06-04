from __future__ import annotations

from datetime import date, datetime, timedelta

from app.auction_pick.engine import AuctionPickEngine
from app.auction_pick.models import AuctionPickResult
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

    def get_stock_list(self):
        return self.stocks

    def get_trade_dates(self, start_date, end_date):
        return [d for d in self.trade_dates if start_date <= d <= end_date]

    def get_daily_bars(self, symbol, start_date, end_date):
        self.daily_bar_symbols.append(symbol)
        self.daily_bar_ranges.append((symbol, start_date, end_date))
        dates = [d for d in self.trade_dates if start_date <= d <= end_date]
        close = 10.0
        bars = []
        for trade_date in dates:
            close = close * (0.995 if symbol == "000004" else 1.002)
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
