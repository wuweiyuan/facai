from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class IntradayQuote:
    symbol: str
    name: str
    latest: float
    previous_close: float
    open: float
    high: float
    low: float
    volume: float
    amount: float
    turnover_rate: float | None = None
    snapshot_time: datetime | None = None

    @property
    def intraday_return(self) -> float:
        if self.previous_close <= 0:
            return 0.0
        return round(self.latest / self.previous_close - 1.0, 10)


@dataclass(frozen=True)
class TailPickResult:
    trade_date: date
    quote: IntradayQuote
    score: float
    entry_price: float
    stop_loss_price: float
    reasons: list[str]

    @property
    def intraday_return(self) -> float:
        return self.quote.intraday_return

    @property
    def next_day_sell_rules(self) -> list[str]:
        return [
            f"跌破 {self.stop_loss_price:.2f} 立即卖出",
            "高开 2% 以上，冲高无力先卖",
            "平开/小高开，15-30 分钟不能放量走强就卖",
            "低开但未破止损，10-15 分钟修复不了卖",
            "10:30 前仍不强，默认离场",
            "涨停或接近涨停且封单稳定，可持有到尾盘再看",
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.quote.symbol,
            "name": self.quote.name,
            "latest": round(self.quote.latest, 4),
            "previous_close": round(self.quote.previous_close, 4),
            "intraday_return": round(self.intraday_return, 6),
            "score": round(self.score, 2),
            "entry_price": round(self.entry_price, 4),
            "stop_loss_price": round(self.stop_loss_price, 4),
            "snapshot_time": self.quote.snapshot_time.isoformat() if self.quote.snapshot_time else None,
            "reasons": list(self.reasons),
            "next_day_sell_rules": self.next_day_sell_rules,
        }
