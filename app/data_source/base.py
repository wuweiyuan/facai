from __future__ import annotations

from datetime import date
from typing import Protocol

from app.models import DailyBar, StockInfo
from app.tail_pick.models import IntradayQuote


class MarketDataSource(Protocol):
    def get_stock_list(self) -> list[StockInfo]:
        ...

    def get_trade_dates(self, start_date: date, end_date: date) -> list[date]:
        ...

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[DailyBar]:
        ...

    def get_index_closes(self, symbol: str, start_date: date, end_date: date) -> dict[date, float]:
        ...


class IntradayQuoteDataSource(Protocol):
    def get_intraday_quotes(self) -> list[IntradayQuote]:
        ...
