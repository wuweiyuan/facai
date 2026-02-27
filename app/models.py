from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    name: str
    listing_date: date | None = None
    is_st: bool = False
    is_paused: bool = False
    market: str | None = None


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover_rate: float | None = None


@dataclass(frozen=True)
class CandidateScore:
    symbol: str
    name: str
    score_total: float
    score_breakdown: dict[str, float]
    key_metrics: dict[str, float]
    reason: list[str]


@dataclass(frozen=True)
class RecommendationResult:
    trade_date: date
    symbol: str
    name: str
    score_total: float
    score_breakdown: dict[str, float]
    key_metrics: dict[str, float]
    reason: list[str]
    threshold_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.symbol,
            "name": self.name,
            "score_total": round(self.score_total, 2),
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
            "key_metrics": {k: round(v, 4) for k, v in self.key_metrics.items()},
            "reason": self.reason,
            "threshold_mode": self.threshold_mode,
        }


@dataclass(frozen=True)
class BacktestRecord:
    trade_date: date
    symbol: str
    name: str
    threshold_mode: str
    ret_1d_gross: float | None
    ret_5d_gross: float | None
    ret_1d_net: float | None
    ret_5d_net: float | None
