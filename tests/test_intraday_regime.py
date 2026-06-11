from __future__ import annotations

from datetime import date, datetime

from app.intraday_regime.engine import IntradayRegimeEngine
from app.main import _print_intraday_regime_hint, build_parser
from app.models import StockInfo
from app.tail_pick.models import IntradayQuote


class FakeIntradayRegimeDataSource:
    def __init__(self, quotes: list[IntradayQuote]):
        self.quotes = quotes
        self.stocks = [StockInfo(symbol=q.symbol, name=q.name) for q in quotes]

    def get_stock_list(self):
        return self.stocks

    def get_intraday_quotes(self):
        return self.quotes


def _quote(symbol: str, latest: float, previous_close: float = 10.0, open_price: float = 10.05) -> IntradayQuote:
    return IntradayQuote(
        symbol=symbol,
        name=f"Stock{symbol}",
        latest=latest,
        previous_close=previous_close,
        open=open_price,
        high=max(latest, open_price, previous_close),
        low=min(latest, open_price, previous_close),
        volume=1_000_000,
        amount=25_000_000,
        turnover_rate=2.0,
        snapshot_time=datetime(2026, 6, 11, 9, 31),
    )


def test_intraday_regime_returns_attack_when_breadth_and_strength_are_high():
    quotes = [_quote(f"{idx:06d}", 10.4) for idx in range(1, 71)]
    quotes += [_quote(f"{idx:06d}", 9.9) for idx in range(71, 101)]
    ds = FakeIntradayRegimeDataSource(quotes)

    payload = IntradayRegimeEngine(ds, {}).evaluate(date(2026, 6, 11), session="morning")

    assert payload.decision == "attack"
    assert payload.decision_zh == "进攻"
    assert payload.metrics["up_ratio"] == 0.7
    assert payload.advice["auction-pick"] == "可正常观察候选"
    assert payload.advice["tail-pick"] == "尾盘可小仓参与，但仍需候选宽度确认"


def test_intraday_regime_returns_cash_when_snapshot_is_weak():
    quotes = [_quote(f"{idx:06d}", 9.7) for idx in range(1, 71)]
    quotes += [_quote(f"{idx:06d}", 10.1) for idx in range(71, 101)]
    ds = FakeIntradayRegimeDataSource(quotes)

    payload = IntradayRegimeEngine(ds, {}).evaluate(date(2026, 6, 11), session="tail")

    assert payload.decision == "cash"
    assert payload.decision_zh == "空仓"
    assert payload.metrics["down_ratio"] == 0.7
    assert payload.advice["auction-pick"] == "不适合追强，候选只观察"
    assert payload.advice["tail-pick"] == "不适合隔夜追强"


def test_intraday_regime_parser_accepts_session_and_output():
    args = build_parser().parse_args(["intraday-regime", "--date", "2026-06-11", "--session", "tail", "--output", "json"])

    assert args.cmd == "intraday-regime"
    assert args.date == "2026-06-11"
    assert args.session == "tail"
    assert args.output == "json"


def test_intraday_regime_hint_prints_strategy_specific_advice(capsys):
    quotes = [_quote(f"{idx:06d}", 10.4) for idx in range(1, 71)]
    quotes += [_quote(f"{idx:06d}", 9.9) for idx in range(71, 101)]
    ds = FakeIntradayRegimeDataSource(quotes)

    _print_intraday_regime_hint({}, ds, date(2026, 6, 11), session="morning", strategy="auction-pick")

    captured = capsys.readouterr()
    assert "[出手提示] 结论=进攻" in captured.out
    assert "auction-pick: 可正常观察候选" in captured.out
