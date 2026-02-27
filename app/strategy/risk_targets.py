from __future__ import annotations

import math


def compute_stop_take_prices(close: float, atr14: float, cfg: dict) -> tuple[float, float]:
    rcfg = cfg.get("risk_targets", {})
    method = str(rcfg.get("method", "atr"))
    tick = float(rcfg.get("price_round_tick", 0.01))
    if close <= 0:
        return 0.0, 0.0

    if method == "percent":
        stop_pct = float(rcfg.get("stop_loss_pct", 0.03))
        take_pct = float(rcfg.get("take_profit_pct", 0.06))
        stop = close * (1.0 - max(stop_pct, 0.0))
        take = close * (1.0 + max(take_pct, 0.0))
    else:
        atr = atr14 if atr14 > 0 else close * 0.02
        stop_mult = float(rcfg.get("stop_loss_atr_mult", 1.5))
        take_mult = float(rcfg.get("take_profit_atr_mult", 3.0))
        stop = close - max(stop_mult, 0.0) * atr
        take = close + max(take_mult, 0.0) * atr

    return _round_price(stop, tick), _round_price(take, tick)


def _round_price(v: float, tick: float) -> float:
    if tick <= 0:
        return float(v)
    return math.floor(v / tick + 1e-9) * tick

