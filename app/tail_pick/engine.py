from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from app.features.indicators import add_indicators, bars_to_df
from app.tail_pick.models import IntradayQuote, TailPickResult
from app.universe.filtering import filter_universe


class TailPickDataSource(Protocol):
    def get_stock_list(self) -> list[Any]:
        ...

    def get_trade_dates(self, start_date: date, end_date: date) -> list[date]:
        ...

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[Any]:
        ...

    def get_intraday_quotes(self) -> list[IntradayQuote]:
        ...


@dataclass(frozen=True)
class TailPickPayload:
    trade_date: date
    selected: TailPickResult | None
    candidates_scanned: int
    candidates_passed: int
    filters: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "selected": self.selected.as_dict() if self.selected else None,
            "candidates_scanned": self.candidates_scanned,
            "candidates_passed": self.candidates_passed,
            "filters": dict(self.filters),
        }


class TailPickEngine:
    def __init__(self, data_source: TailPickDataSource, cfg: dict[str, Any]):
        self.data_source = data_source
        self.cfg = cfg

    def pick(self, trade_date: date) -> TailPickPayload:
        filters = self._filters()
        daily_end_date = self._resolve_completed_daily_date(trade_date)
        universe = filter_universe(self.data_source.get_stock_list(), self.cfg, trade_date)
        allowed_symbols = {stock.symbol for stock in universe}
        quotes = [q for q in self.data_source.get_intraday_quotes() if q.symbol in allowed_symbols]
        scanned_count = len(quotes)
        quotes = self._prefilter_quotes(quotes, filters)
        ranked: list[TailPickResult] = []
        for quote in quotes:
            result = self._score_quote(quote, trade_date, daily_end_date, filters)
            if result is not None:
                ranked.append(result)
        ranked.sort(key=lambda item: (-item.score, item.quote.symbol))
        return TailPickPayload(
            trade_date=trade_date,
            selected=ranked[0] if ranked else None,
            candidates_scanned=scanned_count,
            candidates_passed=len(ranked),
            filters=filters,
        )

    def _filters(self) -> dict[str, float]:
        cfg = self.cfg.get("tail_pick", {}) if isinstance(self.cfg.get("tail_pick", {}), dict) else {}
        return {
            "min_intraday_return": float(cfg.get("min_intraday_return", 0.0)),
            "max_intraday_return": float(cfg.get("max_intraday_return", 0.07)),
            "min_amount": float(cfg.get("min_amount", 10_000_000)),
            "stop_loss_pct": float(cfg.get("stop_loss_pct", 0.04)),
            "max_snapshot_candidates": float(cfg.get("max_snapshot_candidates", 60)),
        }

    def _resolve_completed_daily_date(self, trade_date: date) -> date:
        dates = self.data_source.get_trade_dates(trade_date - timedelta(days=30), trade_date)
        completed = [item for item in dates if item < trade_date]
        if completed:
            return completed[-1]
        return trade_date - timedelta(days=1)

    @staticmethod
    def _prefilter_quotes(
        quotes: list[IntradayQuote],
        filters: dict[str, float],
    ) -> list[IntradayQuote]:
        out: list[IntradayQuote] = []
        for quote in quotes:
            if quote.latest <= 0 or quote.previous_close <= 0 or quote.volume <= 0 or quote.amount <= 0:
                continue
            intraday_return = quote.intraday_return
            if intraday_return < filters["min_intraday_return"] or intraday_return > filters["max_intraday_return"]:
                continue
            if quote.amount < filters["min_amount"]:
                continue
            out.append(quote)
        out.sort(key=lambda item: (-item.amount, item.symbol))
        max_candidates = max(int(filters.get("max_snapshot_candidates", 60)), 1)
        return out[:max_candidates]

    def _score_quote(
        self,
        quote: IntradayQuote,
        trade_date: date,
        daily_end_date: date,
        filters: dict[str, float],
    ) -> TailPickResult | None:
        intraday_return = quote.intraday_return

        bars = self.data_source.get_daily_bars(quote.symbol, daily_end_date - timedelta(days=160), daily_end_date)
        df = add_indicators(bars_to_df(bars))
        if df.empty:
            return None
        latest_daily = df.iloc[-1]
        close = float(latest_daily["close"])
        ma20 = float(latest_daily["ma20"])
        ma60 = float(latest_daily["ma60"])
        if close < ma20 or ma20 < ma60:
            return None

        amount_score = min(quote.amount / 50_000_000, 1.0) * 30.0
        return_score = max(intraday_return, 0.0) / max(filters["max_intraday_return"], 0.01) * 35.0
        trend_score = min(max(close / ma20 - 1.0, 0.0), 0.08) / 0.08 * 35.0
        score = amount_score + return_score + trend_score
        return TailPickResult(
            trade_date=trade_date,
            quote=quote,
            score=score,
            entry_price=quote.latest,
            stop_loss_price=quote.latest * (1.0 - filters["stop_loss_pct"]),
            reasons=[
                f"tail-session gain {intraday_return:.2%}",
                f"amount {quote.amount / 10000:.0f}w",
                "daily trend above MA20/MA60",
            ],
        )
