from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.tail_pick.models import IntradayQuote


@dataclass(frozen=True)
class AuctionPickResult:
    trade_date: date
    quote: IntradayQuote
    score: float
    opening_gap: float
    current_return: float
    reasons: list[str]

    @property
    def execution_notes(self) -> list[str]:
        return [
            "9:30-9:35 不破开盘价和分时均价线再考虑试仓",
            "跌破开盘价或竞价强势快速消失，放弃买入",
            "板块没有同步走强时，只观察不追高",
            "首笔仓位控制在计划仓位的 1/3 到 1/2",
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.quote.symbol,
            "name": self.quote.name,
            "latest": round(self.quote.latest, 4),
            "open": round(self.quote.open, 4),
            "previous_close": round(self.quote.previous_close, 4),
            "opening_gap": round(self.opening_gap, 6),
            "current_return": round(self.current_return, 6),
            "amount": round(self.quote.amount, 2),
            "score": round(self.score, 2),
            "snapshot_time": self.quote.snapshot_time.isoformat() if self.quote.snapshot_time else None,
            "reasons": list(self.reasons),
            "execution_notes": self.execution_notes,
        }


@dataclass(frozen=True)
class AuctionPickPayload:
    trade_date: date
    selected: list[AuctionPickResult]
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
