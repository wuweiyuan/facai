from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any, Protocol

from app.intraday_regime.models import IntradayRegimePayload
from app.tail_pick.models import IntradayQuote
from app.universe.filtering import filter_universe


class IntradayRegimeDataSource(Protocol):
    def get_stock_list(self) -> list[Any]:
        ...

    def get_intraday_quotes(self) -> list[IntradayQuote]:
        ...


class IntradayRegimeEngine:
    def __init__(self, data_source: IntradayRegimeDataSource, cfg: dict[str, Any]):
        self.data_source = data_source
        self.cfg = cfg

    def evaluate(self, trade_date: date, session: str = "morning") -> IntradayRegimePayload:
        session = session if session in {"morning", "tail"} else "morning"
        thresholds = self._thresholds(session)
        universe = filter_universe(self.data_source.get_stock_list(), self.cfg, trade_date)
        allowed_symbols = {stock.symbol for stock in universe}
        quotes = [
            quote
            for quote in self.data_source.get_intraday_quotes()
            if quote.symbol in allowed_symbols and quote.latest > 0 and quote.previous_close > 0 and quote.open > 0
        ]
        metrics = self._metrics(quotes, thresholds)
        score = self._score(metrics, thresholds)
        decision = self._decision(metrics, score, thresholds)
        decision_zh = {"attack": "进攻", "observe": "观察", "cash": "空仓"}[decision]
        return IntradayRegimePayload(
            trade_date=trade_date,
            session=session,
            decision=decision,
            decision_zh=decision_zh,
            score=score,
            metrics=metrics,
            advice=self._advice(decision),
            reasons=self._reasons(metrics, thresholds, decision),
        )

    def _thresholds(self, session: str) -> dict[str, float]:
        cfg = self.cfg.get("intraday_regime", {}) if isinstance(self.cfg.get("intraday_regime", {}), dict) else {}
        session_cfg = cfg.get(session, {}) if isinstance(cfg.get(session, {}), dict) else {}
        defaults = {
            "attack_up_ratio": 0.58 if session == "morning" else 0.60,
            "observe_up_ratio": 0.48 if session == "morning" else 0.50,
            "attack_strong_count": 60 if session == "morning" else 80,
            "observe_strong_count": 25 if session == "morning" else 35,
            "max_attack_weak_ratio": 0.12,
            "max_observe_weak_ratio": 0.20,
            "strong_return": 0.02,
            "weak_return": -0.02,
            "min_above_open_ratio": 0.52 if session == "morning" else 0.55,
        }
        return {key: float(session_cfg.get(key, value)) for key, value in defaults.items()}

    @staticmethod
    def _metrics(quotes: list[IntradayQuote], thresholds: dict[str, float]) -> dict[str, float]:
        total = len(quotes)
        if total == 0:
            return {
                "total": 0.0,
                "up_ratio": 0.0,
                "down_ratio": 0.0,
                "strong_count": 0.0,
                "weak_count": 0.0,
                "weak_ratio": 0.0,
                "above_open_ratio": 0.0,
                "avg_return": 0.0,
            }
        returns = [quote.intraday_return for quote in quotes]
        up_count = sum(1 for item in returns if item > 0)
        down_count = sum(1 for item in returns if item < 0)
        strong_count = sum(1 for item in returns if item >= thresholds["strong_return"])
        weak_count = sum(1 for item in returns if item <= thresholds["weak_return"])
        above_open_count = sum(1 for quote in quotes if quote.latest >= quote.open)
        return {
            "total": float(total),
            "up_ratio": up_count / total,
            "down_ratio": down_count / total,
            "strong_count": float(strong_count),
            "weak_count": float(weak_count),
            "weak_ratio": weak_count / total,
            "above_open_ratio": above_open_count / total,
            "avg_return": mean(returns),
        }

    @staticmethod
    def _score(metrics: dict[str, float], thresholds: dict[str, float]) -> float:
        up_score = min(metrics["up_ratio"] / max(thresholds["attack_up_ratio"], 0.001), 1.0) * 35.0
        strong_score = min(metrics["strong_count"] / max(thresholds["attack_strong_count"], 1.0), 1.0) * 30.0
        open_score = min(metrics["above_open_ratio"] / max(thresholds["min_above_open_ratio"], 0.001), 1.0) * 20.0
        weak_penalty = min(metrics["weak_ratio"] / max(thresholds["max_observe_weak_ratio"], 0.001), 1.0) * 25.0
        avg_bonus = max(min(metrics["avg_return"], 0.02), -0.02) / 0.02 * 10.0
        return max(up_score + strong_score + open_score + avg_bonus - weak_penalty, 0.0)

    @staticmethod
    def _decision(metrics: dict[str, float], score: float, thresholds: dict[str, float]) -> str:
        if metrics["total"] <= 0:
            return "cash"
        attack = (
            metrics["up_ratio"] >= thresholds["attack_up_ratio"]
            and metrics["strong_count"] >= thresholds["attack_strong_count"]
            and metrics["weak_ratio"] <= thresholds["max_attack_weak_ratio"]
            and metrics["above_open_ratio"] >= thresholds["min_above_open_ratio"]
            and score >= 70.0
        )
        if attack:
            return "attack"
        observe = (
            metrics["up_ratio"] >= thresholds["observe_up_ratio"]
            and metrics["strong_count"] >= thresholds["observe_strong_count"]
            and metrics["weak_ratio"] <= thresholds["max_observe_weak_ratio"]
            and score >= 45.0
        )
        if observe:
            return "observe"
        return "cash"

    @staticmethod
    def _advice(decision: str) -> dict[str, str]:
        if decision == "attack":
            return {
                "auction-pick": "可正常观察候选",
                "tail-pick": "尾盘可小仓参与，但仍需候选宽度确认",
            }
        if decision == "observe":
            return {
                "auction-pick": "只做最强候选，降低仓位",
                "tail-pick": "只观察，除非主线和候选宽度都很强",
            }
        return {
            "auction-pick": "不适合追强，候选只观察",
            "tail-pick": "不适合隔夜追强",
        }

    @staticmethod
    def _reasons(metrics: dict[str, float], thresholds: dict[str, float], decision: str) -> list[str]:
        reasons = [
            f"上涨占比 {metrics['up_ratio']:.1%}",
            f"强势票数量 {int(metrics['strong_count'])}",
            f"弱势票数量 {int(metrics['weak_count'])} ({metrics['weak_ratio']:.1%})",
            f"站上开盘价占比 {metrics['above_open_ratio']:.1%}",
        ]
        if decision == "cash":
            reasons.append("短线宽度或承接不足，不适合追强")
        elif decision == "observe":
            reasons.append("短线环境一般，适合降仓观察")
        else:
            reasons.append("短线宽度和承接达到进攻阈值")
        reasons.append(
            f"进攻阈值: 上涨占比>={thresholds['attack_up_ratio']:.0%}, "
            f"强势票>={int(thresholds['attack_strong_count'])}, "
            f"弱势票占比<={thresholds['max_attack_weak_ratio']:.0%}"
        )
        return reasons
