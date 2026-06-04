from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from app.auction_pick.models import AuctionPickPayload, AuctionPickResult
from app.features.indicators import add_indicators, bars_to_df
from app.tail_pick.models import IntradayQuote
from app.universe.filtering import filter_universe


class AuctionPickDataSource(Protocol):
    def get_stock_list(self) -> list[Any]:
        ...

    def get_trade_dates(self, start_date: date, end_date: date) -> list[date]:
        ...

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[Any]:
        ...

    def get_intraday_quotes(self) -> list[IntradayQuote]:
        ...


@dataclass(frozen=True)
class _ScoredQuote:
    result: AuctionPickResult
    amount: float


class AuctionPickEngine:
    def __init__(self, data_source: AuctionPickDataSource, cfg: dict[str, Any]):
        self.data_source = data_source
        self.cfg = cfg

    def pick(self, trade_date: date, count: int | None = None) -> AuctionPickPayload:
        filters = self._filters()
        pick_count = max(int(count if count is not None else filters["count"]), 1)
        daily_end_date = self._resolve_completed_daily_date(trade_date)
        universe = filter_universe(self.data_source.get_stock_list(), self.cfg, trade_date)
        allowed_symbols = {stock.symbol for stock in universe}
        quotes = [q for q in self.data_source.get_intraday_quotes() if q.symbol in allowed_symbols]
        scanned_count = len(quotes)
        quotes = self._prefilter_quotes(quotes, filters)
        ranked: list[_ScoredQuote] = []
        for quote in quotes:
            result = self._score_quote(quote, trade_date, daily_end_date, filters)
            if result is not None:
                ranked.append(_ScoredQuote(result=result, amount=quote.amount))
        ranked.sort(key=lambda item: (-item.result.score, -item.amount, item.result.quote.symbol))
        selected = [item.result for item in ranked[:pick_count]]
        return AuctionPickPayload(
            trade_date=trade_date,
            selected=selected,
            candidates_scanned=scanned_count,
            candidates_passed=len(ranked),
            filters=filters,
        )

    def _filters(self) -> dict[str, float]:
        cfg = self.cfg.get("auction_pick", {}) if isinstance(self.cfg.get("auction_pick", {}), dict) else {}
        return {
            "count": float(cfg.get("count", 2)),
            "min_opening_gap": float(cfg.get("min_opening_gap", 0.012)),
            "max_opening_gap": float(cfg.get("max_opening_gap", 0.04)),
            "min_current_return": float(cfg.get("min_current_return", 0.012)),
            "max_current_return": float(cfg.get("max_current_return", 0.055)),
            "min_amount": float(cfg.get("min_amount", 20_000_000)),
            "min_latest_vs_open": float(cfg.get("min_latest_vs_open", 1.0)),
            "max_snapshot_candidates": float(cfg.get("max_snapshot_candidates", 80)),
            "limit_up_return": float(cfg.get("limit_up_return", 0.09)),
            "max_close_above_ma20_pct": float(cfg.get("max_close_above_ma20_pct", 0.08)),
            "max_rsi14": float(cfg.get("max_rsi14", 75)),
            "min_ma20_slope5": float(cfg.get("min_ma20_slope5", 0.0)),
        }

    def _resolve_completed_daily_date(self, trade_date: date) -> date:
        dates = self.data_source.get_trade_dates(trade_date - timedelta(days=30), trade_date)
        completed = [item for item in dates if item < trade_date]
        if completed:
            return completed[-1]
        return trade_date - timedelta(days=1)

    @staticmethod
    def _prefilter_quotes(quotes: list[IntradayQuote], filters: dict[str, float]) -> list[IntradayQuote]:
        out: list[IntradayQuote] = []
        for quote in quotes:
            if (
                quote.latest <= 0
                or quote.previous_close <= 0
                or quote.open <= 0
                or quote.volume <= 0
                or quote.amount <= 0
            ):
                continue
            opening_gap = quote.open / quote.previous_close - 1.0
            current_return = quote.latest / quote.previous_close - 1.0
            if opening_gap < filters["min_opening_gap"] or opening_gap > filters["max_opening_gap"]:
                continue
            if current_return < filters["min_current_return"] or current_return > filters["max_current_return"]:
                continue
            if current_return >= filters["limit_up_return"]:
                continue
            if quote.amount < filters["min_amount"]:
                continue
            if quote.latest < quote.open * filters["min_latest_vs_open"]:
                continue
            out.append(quote)
        out.sort(key=lambda item: (-item.amount, item.symbol))
        max_candidates = max(int(filters.get("max_snapshot_candidates", 80)), 1)
        return out[:max_candidates]

    def _score_quote(
        self,
        quote: IntradayQuote,
        trade_date: date,
        daily_end_date: date,
        filters: dict[str, float],
    ) -> AuctionPickResult | None:
        bars = self.data_source.get_daily_bars(quote.symbol, daily_end_date - timedelta(days=160), daily_end_date)
        df = add_indicators(bars_to_df(bars))
        if df.empty:
            return None
        latest_daily = df.iloc[-1]
        close = float(latest_daily["close"])
        ma20 = float(latest_daily["ma20"])
        ma60 = float(latest_daily["ma60"])
        rsi14 = float(latest_daily["rsi14"])
        ma20_slope5 = float(latest_daily["ma20_slope5"])
        distance_above_ma20 = close / ma20 - 1.0 if ma20 > 0 else 0.0
        if close < ma20 or ma20 < ma60:
            return None
        if distance_above_ma20 > filters["max_close_above_ma20_pct"]:
            return None
        if rsi14 == rsi14 and rsi14 > filters["max_rsi14"]:
            return None
        if ma20_slope5 != ma20_slope5 or ma20_slope5 < filters["min_ma20_slope5"]:
            return None

        opening_gap = quote.open / quote.previous_close - 1.0
        current_return = quote.latest / quote.previous_close - 1.0
        gap_span = max(filters["max_opening_gap"] - filters["min_opening_gap"], 0.001)
        gap_mid = (filters["min_opening_gap"] + filters["max_opening_gap"]) / 2.0
        gap_score = max(1.0 - abs(opening_gap - gap_mid) / gap_span, 0.0) * 25.0
        return_center = (filters["min_current_return"] + filters["max_current_return"]) / 2.0
        return_half_width = max((filters["max_current_return"] - filters["min_current_return"]) / 2.0, 0.001)
        return_score = max(1.0 - abs(current_return - return_center) / return_half_width, 0.0) * 25.0
        amount_score = min(quote.amount / 80_000_000, 1.0) * 20.0
        trend_score = 20.0 + min(max(ma20_slope5, 0.0) / 0.03, 1.0) * 10.0
        fade_penalty = max((quote.open - quote.latest) / quote.open, 0.0) * 150.0
        score = gap_score + return_score + amount_score + trend_score - fade_penalty
        return AuctionPickResult(
            trade_date=trade_date,
            quote=quote,
            score=score,
            opening_gap=opening_gap,
            current_return=current_return,
            reasons=[
                f"opening gap {opening_gap:.2%}",
                f"current return {current_return:.2%}",
                f"amount {quote.amount / 10000:.0f}w",
                "daily trend passes MA20/MA60 filter",
            ],
        )
