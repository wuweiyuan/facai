from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class IntradayRegimePayload:
    trade_date: date
    session: str
    decision: str
    decision_zh: str
    score: float
    metrics: dict[str, float]
    advice: dict[str, str]
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "session": self.session,
            "decision": self.decision,
            "decision_zh": self.decision_zh,
            "score": round(self.score, 2),
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "advice": dict(self.advice),
            "reasons": list(self.reasons),
        }
