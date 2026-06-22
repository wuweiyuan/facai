from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import sys
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
    selected: list[TailPickResult]
    candidates_scanned: int
    candidates_passed: int
    filters: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "selected": [item.as_dict() for item in self.selected],
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
        pick_count = max(int(filters.get("count", 2)), 1)
        min_required_candidates = max(int(filters.get("min_required_candidates", 1)), 1)
        selected = ranked[:pick_count] if len(ranked) >= min_required_candidates else []
        return TailPickPayload(
            trade_date=trade_date,
            selected=selected,
            candidates_scanned=scanned_count,
            candidates_passed=len(ranked),
            filters=filters,
        )

    def _filters(self) -> dict[str, float]:
        cfg = self.cfg.get("tail_pick", {}) if isinstance(self.cfg.get("tail_pick", {}), dict) else {}
        amount_tiers = cfg.get("amount_tiers", {}) if isinstance(cfg.get("amount_tiers", {}), dict) else {}
        return {
            "min_intraday_return": float(cfg.get("min_intraday_return", 0.01)),
            "max_intraday_return": float(cfg.get("max_intraday_return", 0.06)),
            "min_amount": float(cfg.get("min_amount", 20_000_000)),
            "min_turnover_rate": float(cfg.get("min_turnover_rate", 0.0)),
            "max_turnover_rate": float(cfg.get("max_turnover_rate", 100.0)),
            "amount_tiers_enabled": float(1.0 if bool(amount_tiers.get("enabled", False)) else 0.0),
            "small_cap_max": float(amount_tiers.get("small_cap_max", 5_000_000_000)),
            "mid_cap_max": float(amount_tiers.get("mid_cap_max", 20_000_000_000)),
            "small_cap_min_amount": float(amount_tiers.get("small_cap_min_amount", 50_000_000)),
            "mid_cap_min_amount": float(amount_tiers.get("mid_cap_min_amount", 80_000_000)),
            "large_cap_min_amount": float(amount_tiers.get("large_cap_min_amount", 120_000_000)),
            "low_price_max": float(amount_tiers.get("low_price_max", 10)),
            "mid_price_max": float(amount_tiers.get("mid_price_max", 50)),
            "low_price_min_amount": float(amount_tiers.get("low_price_min_amount", 50_000_000)),
            "mid_price_min_amount": float(amount_tiers.get("mid_price_min_amount", 80_000_000)),
            "high_price_min_amount": float(amount_tiers.get("high_price_min_amount", 120_000_000)),
            "stop_loss_pct": float(cfg.get("stop_loss_pct", 0.04)),
            "max_snapshot_candidates": float(cfg.get("max_snapshot_candidates", 60)),
            "min_latest_vs_open": float(cfg.get("min_latest_vs_open", 1.0)),
            "min_close_position": float(cfg.get("min_close_position", 0.65)),
            "max_fade_from_high": float(cfg.get("max_fade_from_high", 0.025)),
            "max_close_above_ma20_pct": float(cfg.get("max_close_above_ma20_pct", 0.10)),
            "max_rsi14": float(cfg.get("max_rsi14", 78)),
            "min_ma20_slope5": float(cfg.get("min_ma20_slope5", 0.0)),
            "min_intraday_volume_ratio_20": float(cfg.get("min_intraday_volume_ratio_20", 0.0)),
            "max_current_return_3d": float(cfg.get("max_current_return_3d", 99.0)),
            "max_current_return_5d": float(cfg.get("max_current_return_5d", 99.0)),
            "min_score": float(cfg.get("min_score", 0.0)),
            "count": float(cfg.get("count", 2)),
            "min_required_candidates": float(cfg.get("min_required_candidates", 1)),
        }

    def _resolve_completed_daily_date(self, trade_date: date) -> date:
        dates = self.data_source.get_trade_dates(trade_date - timedelta(days=30), trade_date)
        completed = [item for item in dates if item < trade_date]
        if completed:
            return completed[-1]
        return trade_date - timedelta(days=1)

    @staticmethod
    def _close_position(quote: IntradayQuote) -> float:
        span = quote.high - quote.low
        if span <= 0:
            return 0.0
        return (quote.latest - quote.low) / span

    @staticmethod
    def _prefilter_quotes(
        quotes: list[IntradayQuote],
        filters: dict[str, float],
    ) -> list[IntradayQuote]:
        out: list[IntradayQuote] = []
        for quote in quotes:
            if (
                quote.latest <= 0
                or quote.previous_close <= 0
                or quote.open <= 0
                or quote.high <= 0
                or quote.low <= 0
                or quote.volume <= 0
                or quote.amount <= 0
            ):
                continue
            intraday_return = quote.intraday_return
            if intraday_return < filters["min_intraday_return"] or intraday_return > filters["max_intraday_return"]:
                continue
            if quote.amount < filters["min_amount"]:
                continue
            if quote.amount < TailPickEngine._required_amount(quote, filters):
                continue
            if not TailPickEngine._passes_turnover_filter(quote, filters):
                continue
            if quote.latest < quote.open * filters["min_latest_vs_open"]:
                continue
            if TailPickEngine._close_position(quote) < filters["min_close_position"]:
                continue
            if quote.high > 0 and quote.latest / quote.high - 1.0 < -filters["max_fade_from_high"]:
                continue
            out.append(quote)
        out.sort(key=lambda item: (-item.amount, item.symbol))
        max_candidates = max(int(filters.get("max_snapshot_candidates", 60)), 1)
        return out[:max_candidates]

    @staticmethod
    def _required_amount(quote: IntradayQuote, filters: dict[str, float]) -> float:
        base_amount = filters["min_amount"]
        if filters.get("amount_tiers_enabled", 0.0) <= 0:
            return base_amount
        market_cap = quote.float_market_cap or quote.total_market_cap
        if market_cap and market_cap > 0:
            if market_cap <= filters["small_cap_max"]:
                return max(base_amount, filters["small_cap_min_amount"])
            if market_cap <= filters["mid_cap_max"]:
                return max(base_amount, filters["mid_cap_min_amount"])
            return max(base_amount, filters["large_cap_min_amount"])
        price = quote.latest
        if price <= filters["low_price_max"]:
            return max(base_amount, filters["low_price_min_amount"])
        if price <= filters["mid_price_max"]:
            return max(base_amount, filters["mid_price_min_amount"])
        return max(base_amount, filters["high_price_min_amount"])

    def _score_quote(
        self,
        quote: IntradayQuote,
        trade_date: date,
        daily_end_date: date,
        filters: dict[str, float],
    ) -> TailPickResult | None:
        intraday_return = quote.intraday_return

        try:
            bars = self.data_source.get_daily_bars(quote.symbol, daily_end_date - timedelta(days=160), daily_end_date)
        except Exception as exc:
            print(f"[尾盘] 跳过 {quote.symbol} {quote.name}: 日线数据获取失败: {exc}", file=sys.stderr)
            return None
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
        vol_ma20 = float(latest_daily["vol_ma20"])
        if filters["min_intraday_volume_ratio_20"] > 0:
            if vol_ma20 != vol_ma20 or vol_ma20 <= 0:
                return None
            intraday_volume_ratio_20 = quote.volume / vol_ma20
            if intraday_volume_ratio_20 < filters["min_intraday_volume_ratio_20"]:
                return None
        current_return_3d = self._current_return_from_prior_close(df, quote.latest, 3)
        if current_return_3d is not None and current_return_3d > filters["max_current_return_3d"]:
            return None
        current_return_5d = self._current_return_from_prior_close(df, quote.latest, 5)
        if current_return_5d is not None and current_return_5d > filters["max_current_return_5d"]:
            return None

        close_position = self._close_position(quote)
        return_center = (filters["min_intraday_return"] + filters["max_intraday_return"]) / 2.0
        return_half_width = max((filters["max_intraday_return"] - filters["min_intraday_return"]) / 2.0, 0.001)
        amount_score = min(quote.amount / 80_000_000, 1.0) * 25.0
        return_score = max(1.0 - abs(intraday_return - return_center) / return_half_width, 0.0) * 30.0
        position_score = close_position * 20.0
        trend_score = min(max(distance_above_ma20, 0.0), 0.08) / 0.08 * 25.0
        fade_penalty = max(1.0 - quote.latest / quote.high, 0.0) * 100.0 if quote.high > 0 else 0.0
        score = amount_score + return_score + position_score + trend_score - fade_penalty
        if score < filters["min_score"]:
            return None
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

    @staticmethod
    def _passes_turnover_filter(quote: IntradayQuote, filters: dict[str, float]) -> bool:
        min_turnover = filters["min_turnover_rate"]
        max_turnover = filters["max_turnover_rate"]
        if min_turnover <= 0 and max_turnover >= 100:
            return True
        if quote.turnover_rate is None:
            return False
        return min_turnover <= quote.turnover_rate <= max_turnover

    @staticmethod
    def _current_return_from_prior_close(df, current_price: float, periods: int) -> float | None:
        if len(df) < periods:
            return None
        prior_close = float(df.iloc[-periods]["close"])
        if prior_close <= 0:
            return None
        return current_price / prior_close - 1.0
